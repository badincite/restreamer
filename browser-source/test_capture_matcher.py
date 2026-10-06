import unittest

from capture_matcher import CaptureMatcher, Measurement


def sample(time, frames, byte_count, source='blob:one', area=100000):
    return {'time': time, 'frames': frames, 'bytes': byte_count, 'source': source,
            'width': 1920, 'height': 1080, 'area': area}


class MeasurementTests(unittest.TestCase):
    def test_bitrate_uses_media_time_not_bursty_downloads(self):
        m = Measurement()
        for i in range(4):
            result = m.add(sample(i * 5, i * 300, i * 3437500))
        self.assertEqual(result, {'fps': '60', 'bitrate': 5500})

    def test_pause_or_seek_discards_old_windows(self):
        m = Measurement()
        for i in range(3):
            self.assertIsNone(m.add(sample(i * 5, i * 150, i * 1000000)))
        self.assertIsNone(m.add(sample(1, 1, 1)))
        self.assertEqual(m.windows, [])

    def test_missing_byte_counter_still_detects_frame_rate(self):
        m = Measurement()
        for i in range(4):
            result = m.add(sample(i * 5, i * 120, None))
        self.assertEqual(result, {'fps': '24'})


class MatcherTests(unittest.TestCase):
    def matcher(self):
        return CaptureMatcher(None, None, None, None)

    def feed(self, matcher, start, fps, kbps, source='blob:one'):
        result = None
        for i in range(4):
            now = start + i * 5
            matcher.observe('page', sample(i * 5, i * fps * 5, i * kbps * 625, source), now)
            result = matcher.proposal(now)
        return result

    def test_switches_profiles_after_stable_sample(self):
        m = self.matcher()
        self.assertEqual(self.feed(m, 0, 30, 5500)['fps'], '30')
        result = self.feed(m, 35, 60, 8000, 'blob:two')
        self.assertEqual(result['fps'], '60')
        self.assertEqual(result['bitrate'], '8000k')

    def test_clamps_bitrate_and_optional_frame_rate_cap(self):
        m = CaptureMatcher(None, None, None, None, maximum=12000, max_fps=30)
        result = self.feed(m, 0, 60, 20000)
        self.assertEqual(result['bitrate'], '12000k')
        self.assertEqual(result['fps'], '30')

    def test_small_bitrate_fluctuations_do_not_restart(self):
        m = self.matcher()
        self.feed(m, 0, 30, 5500)
        self.assertIsNone(self.feed(m, 35, 30, 5750, 'blob:two'))

    def test_large_scene_bitrate_changes_do_not_restart_same_video(self):
        m = self.matcher()
        self.feed(m, 0, 30, 3250)
        byte_count = 3 * 3250 * 625
        for i in range(4, 16):
            byte_count += (8000 if i < 10 else 2000) * 625
            now = i * 5
            m.observe('page', sample(now, i * 150, byte_count), now)
            self.assertIsNone(m.proposal(now))
        self.assertEqual(m.applied['bitrate'], '3250k')

    def test_frame_rate_change_can_update_same_video(self):
        m = self.matcher()
        self.feed(m, 0, 30, 5500)
        frames = 450
        for i in range(4, 11):
            frames += 300
            now = i * 5
            m.observe('page', sample(now, frames, i * 5500 * 625), now)
            result = m.proposal(now)
            if result:
                self.assertEqual(result['fps'], '60')
        self.assertEqual(m.applied['fps'], '60')

    def test_repeated_sample_does_not_erase_measurement(self):
        m = self.matcher()
        for i in range(4):
            now = i * 5
            m.observe('page', sample(now, i * 150, i * 3437500), now)
            result = m.proposal(now)
            self.assertIsNone(m.proposal(now + .1))
        self.assertEqual(result['bitrate'], '5500k')

    def test_new_bitrate_can_replace_fps_only_profile(self):
        m = self.matcher()
        for i in range(4):
            m.observe('page', sample(i * 5, i * 150, None), i * 5)
            result = m.proposal(i * 5)
        self.assertEqual(result, {'fps': '30'})
        self.assertEqual(self.feed(m, 35, 30, 5500, 'blob:two')['bitrate'], '5500k')


if __name__ == '__main__':
    unittest.main()
