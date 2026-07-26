"""Unit tests for Fibonacci Treeview insert chunking (no Tk required)."""

from __future__ import annotations

import unittest

from mtpmanager.ui.controllers import fibonacci_chunk_bounds


class FibonacciChunkBoundsTests(unittest.TestCase):
    def test_empty_total(self) -> None:
        self.assertEqual(fibonacci_chunk_bounds(0), [])
        self.assertEqual(fibonacci_chunk_bounds(-3), [])

    def test_single_item(self) -> None:
        self.assertEqual(fibonacci_chunk_bounds(1), [(0, 1)])

    def test_fibonacci_sizes_cover_total(self) -> None:
        # Default 1,1,2,3,5,8 for total 20 → 1+1+2+3+5+8 = 20
        bounds = fibonacci_chunk_bounds(20)
        sizes = [e - s for s, e in bounds]
        self.assertEqual(sizes, [1, 1, 2, 3, 5, 8])
        self.assertEqual(bounds[0][0], 0)
        self.assertEqual(bounds[-1][1], 20)
        # Contiguous and complete.
        covered = []
        for s, e in bounds:
            covered.extend(range(s, e))
        self.assertEqual(covered, list(range(20)))

    def test_partial_last_chunk(self) -> None:
        # 1+1+2+3+5 = 12, next would be 8 but only 2 remain for total 14.
        bounds = fibonacci_chunk_bounds(14)
        sizes = [e - s for s, e in bounds]
        self.assertEqual(sizes, [1, 1, 2, 3, 5, 2])
        self.assertEqual(sum(sizes), 14)

    def test_cap_limits_large_slices(self) -> None:
        bounds = fibonacci_chunk_bounds(1000, cap=10)
        sizes = [e - s for s, e in bounds]
        self.assertTrue(all(sz <= 10 for sz in sizes))
        self.assertEqual(sum(sizes), 1000)
        # Early growth still fib-like until cap: 1,1,2,3,5,8,10,10,…
        self.assertEqual(sizes[:7], [1, 1, 2, 3, 5, 8, 10])
        self.assertTrue(all(sz == 10 for sz in sizes[6:]))

    def test_custom_start_pair(self) -> None:
        bounds = fibonacci_chunk_bounds(12, first=2, second=3)
        sizes = [e - s for s, e in bounds]
        # 2,3,5,2 remaining
        self.assertEqual(sizes, [2, 3, 5, 2])


if __name__ == "__main__":
    unittest.main()
