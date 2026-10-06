import importlib.util
import os
import pathlib
import unittest
from unittest import mock


os.environ.setdefault("BROWSER_RTMP_URL", "rtmp://example.invalid/live/test")
path = pathlib.Path(__file__).with_name("browser-publisher.py")
spec = importlib.util.spec_from_file_location("browser_publisher", path)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class SwitchingTests(unittest.TestCase):
    def test_experimental_probe_is_disabled(self):
        self.assertFalse(publisher.ENABLE_DRM_PROBE)

    def test_detects_hls_and_dash_but_not_fragments(self):
        self.assertTrue(publisher.MEDIA_SUFFIX.search("https://x.invalid/live.m3u8?key=1"))
        self.assertTrue(publisher.MEDIA_SUFFIX.search("https://x.invalid/live.mpd?key=1"))
        self.assertFalse(publisher.MEDIA_SUFFIX.search("https://x.invalid/chunk.m4s"))

    def test_new_manifest_replaces_active_one(self):
        queued = [
            {"url": "https://x.invalid/old.m3u8", "headers": {}, "seen": 100},
            {"url": "https://x.invalid/new.mpd", "headers": {"Referer": "https://x.invalid/"}, "seen": 101},
        ]
        chosen = publisher.next_candidate(queued, "https://x.invalid/old.m3u8", {}, 102)
        self.assertEqual(chosen["url"], "https://x.invalid/new.mpd")

    def test_request_headers_win_over_current_src(self):
        queued = [
            {"url": "https://x.invalid/new.m3u8", "headers": {"Referer": "https://x.invalid/"}, "seen": 100},
            {"url": "https://x.invalid/new.m3u8", "headers": {}, "seen": 101},
        ]
        chosen = publisher.next_candidate(queued, None, {}, 102)
        self.assertEqual(chosen["headers"]["Referer"], "https://x.invalid/")

    def test_expired_or_blocked_candidates_are_ignored(self):
        queued = [
            {"url": "https://x.invalid/old.mpd", "seen": 1},
            {"url": "https://x.invalid/blocked.mpd", "seen": 100},
        ]
        self.assertIsNone(publisher.next_candidate(queued, None,
                                                   {"https://x.invalid/blocked.mpd": 200}, 101))

    def test_remaining_candidates_survive_first_choice(self):
        queued = [
            {"url": "https://x.invalid/older.mpd", "seen": 100},
            {"url": "https://x.invalid/newer.mpd", "seen": 101},
        ]
        self.assertEqual(publisher.next_candidate(queued, None, {}, 102)["url"],
                         "https://x.invalid/newer.mpd")
        self.assertEqual(publisher.next_candidate(queued, None, {}, 102)["url"],
                         "https://x.invalid/older.mpd")


class CaptureLifecycleTests(unittest.TestCase):
    def test_nvenc_keeps_screen_audio_and_dynamic_settings(self):
        with mock.patch.dict(os.environ, {"BROWSER_VIDEO_ENCODER": "h264_nvenc"}):
            command = publisher.command_for("capture", settings={"fps": "60", "bitrate": "4000k"})
        self.assertIn("x11grab", command)
        self.assertIn("pulse", command)
        self.assertEqual(command[command.index("-c:v") + 1], "h264_nvenc")
        self.assertEqual(command[command.index("-framerate") + 1], "60")
        self.assertEqual(command[command.index("-b:v") + 1], "4000k")
        self.assertEqual(command[command.index("-bf") + 1], "0")
        self.assertNotIn("zerolatency", command)
        self.assertNotIn("libx264", command)

    def test_unknown_encoder_is_rejected(self):
        with mock.patch.dict(os.environ, {"BROWSER_VIDEO_ENCODER": "unknown"}):
            with self.assertRaises(ValueError):
                publisher.command_for("capture")

    def test_auto_matching_keeps_screen_capture_and_restarts_only_encoder(self):
        process, replacement = mock.Mock(), mock.Mock()
        process.poll.return_value = None
        matcher = mock.Mock()
        profile = {"fps": "60", "bitrate": "5500k"}
        matcher.tick.return_value = profile
        def sleep(_seconds):
            publisher.RUNNING = False
        with mock.patch.object(publisher, "AUTO_MATCH", True), \
                mock.patch.object(publisher, "RUNNING", True), \
                mock.patch.object(publisher, "CaptureMatcher", return_value=matcher), \
                mock.patch.object(publisher, "launch", side_effect=[process, replacement]) as launch, \
                mock.patch.object(publisher, "terminate") as terminate, \
                mock.patch.object(publisher, "playable") as playable, \
                mock.patch.object(publisher.time, "sleep", side_effect=sleep):
            publisher.main()
        self.assertEqual(launch.call_args_list, [mock.call("capture"), mock.call("capture", settings=profile)])
        self.assertEqual(terminate.call_args_list, [mock.call(process), mock.call(replacement)])
        playable.assert_not_called()
        matcher.close.assert_called_once()

    def test_capture_uses_configured_display_audio_and_frame_rate(self):
        with mock.patch.object(publisher, "DISPLAY", ":0"), \
                mock.patch.object(publisher, "AUDIO_SOURCE", "null.monitor"), \
                mock.patch.object(publisher, "SIZE", "1920x1080"):
            for fps in ("24", "30", "60"):
                with mock.patch.object(publisher, "FPS", fps):
                    command = publisher.command_for("capture")
                self.assertEqual(command[command.index("-framerate") + 1], fps)
                self.assertEqual(command[command.index("-video_size") + 1], "1920x1080")
                self.assertIn(":0.0", command)
                self.assertIn("null.monitor", command)
                self.assertLess(command.index("null.monitor"), command.index(":0.0"))
                self.assertIn("libx264", command)
                self.assertNotIn("copy", command)

    def test_failed_publisher_restarts_without_media_discovery(self):
        failed = mock.Mock()
        failed.poll.return_value = 1
        replacement = mock.Mock()

        def sleep(seconds):
            if seconds == 1:
                publisher.RUNNING = False

        with mock.patch.object(publisher, "MODE", "off"), \
                mock.patch.object(publisher, "RUNNING", True), \
                mock.patch.object(publisher, "launch", side_effect=[failed, replacement]) as launch, \
                mock.patch.object(publisher, "terminate") as terminate, \
                mock.patch.object(publisher.time, "sleep", side_effect=sleep), \
                mock.patch.object(publisher, "targets") as targets:
            publisher.main()
            self.assertEqual(launch.call_args_list, [mock.call("capture"), mock.call("capture")])
            terminate.assert_called_once_with(replacement)
            targets.assert_not_called()

    def test_shutdown_during_retry_does_not_start_another_encoder(self):
        failed = mock.Mock()
        failed.poll.return_value = 1

        def sleep(_seconds):
            publisher.RUNNING = False

        with mock.patch.object(publisher, "MODE", "off"), \
                mock.patch.object(publisher, "RUNNING", True), \
                mock.patch.object(publisher, "launch", return_value=failed) as launch, \
                mock.patch.object(publisher, "terminate") as terminate, \
                mock.patch.object(publisher.time, "sleep", side_effect=sleep):
            publisher.main()
            launch.assert_called_once_with("capture")
            terminate.assert_called_once_with(failed)

    def test_encoder_is_terminated_when_control_loop_fails(self):
        process = mock.Mock()
        process.poll.return_value = None
        with mock.patch.object(publisher, "MODE", "off"), \
                mock.patch.object(publisher, "RUNNING", True), \
                mock.patch.object(publisher, "launch", return_value=process), \
                mock.patch.object(publisher, "terminate") as terminate, \
                mock.patch.object(publisher.time, "sleep", side_effect=RuntimeError("test")):
            with self.assertRaises(RuntimeError):
                publisher.main()
            terminate.assert_called_once_with(process)


if __name__ == "__main__":
    unittest.main()
