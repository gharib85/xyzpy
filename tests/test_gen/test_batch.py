from tempfile import TemporaryDirectory

import pytest

from xyzpy import combo_runner
from xyzpy.gen.batch import (
    XYZError,
    Crop,
    parse_crop_details,
    combos_sow,
    grow,
    combos_reap,
    combos_reap_to_ds,
)

from . import foo3_scalar


def foo_add(a, b, c):
    return a + b


class TestSowerReaper:
    @pytest.mark.parametrize(
        "fn, crop_name, crop_loc, expected",
        [
            (foo_add, None, None, '.xyz-foo_add'),
            (None, 'custom', None, '.xyz-custom'),
            (foo_add, 'custom', None, '.xyz-custom'),
            (foo_add, None, 'custom_dir', 'custom_dir/.xyz-foo_add'),
            (None, 'custom', 'custom_dir', 'custom_dir/.xyz-custom'),
            (foo_add, 'custom', 'custom_dir', 'custom_dir/.xyz-custom'),
            (None, None, None, 'raises'),
            (None, None, 'custom_dir', 'raises'),

        ])
    def test_parse_field_details(self, fn, crop_name, crop_loc, expected):

        if expected == 'raises':
            with pytest.raises(ValueError):
                parse_crop_details(fn, crop_name, crop_loc)
        else:
            crop_loc = parse_crop_details(fn, crop_name, crop_loc)[0]
            assert crop_loc[-len(expected):] == expected

    def test_checks(self):
        with pytest.raises(ValueError):
            Crop(name='custom', save_fn=True)

        with pytest.raises(TypeError):
            c = Crop(fn=foo_add, save_fn=False, batchsize=0.5)
            c.choose_batch_settings([('a', [1, 2])])

        with pytest.raises(ValueError):
            c = Crop(fn=foo_add, save_fn=False, batchsize=-1)
            c.choose_batch_settings([('a', [1, 2])])

        with pytest.raises(ValueError):
            c = Crop(fn=foo_add, save_fn=False, batchsize=1, num_batches=2)
            c.choose_batch_settings([('a', [1, 2, 3])])

        with pytest.raises(ValueError):
            c = Crop(fn=foo_add, save_fn=False, batchsize=2, num_batches=3)
            c.choose_batch_settings([('a', [1, 2, 3])])

        c = Crop(fn=foo_add, save_fn=False, batchsize=1, num_batches=3)
        c.choose_batch_settings([('a', [1, 2, 3])])

        c = Crop(fn=foo_add, save_fn=False, batchsize=2, num_batches=2)
        c.choose_batch_settings([('a', [1, 2, 3])])

        c = Crop(fn=foo_add, save_fn=False, batchsize=3, num_batches=1)
        c.choose_batch_settings([('a', [1, 2, 3])])

        with pytest.raises(XYZError):
            grow(1)

    def test_batch(self):

        combos = [
            ('a', [10, 20, 30]),
            ('b', [4, 5, 6, 7]),
        ]
        expected = combo_runner(foo_add, combos, constants={'c': True})

        with TemporaryDirectory() as tdir:

            # sow seeds
            crop = Crop(fn=foo_add, parent_dir=tdir, batchsize=5)

            assert not crop.is_prepared()
            assert crop.num_sown_batches == crop.num_results == -1

            combos_sow(crop, combos, constants={'c': True})

            assert crop.is_prepared()
            assert crop.num_sown_batches == 3
            assert crop.num_results == 0

            # grow seeds
            for i in range(1, 4):
                grow(i, Crop(parent_dir=tdir, name='foo_add'))

                if i == 1:
                    assert crop.missing_results() == (2, 3,)

            assert crop.is_ready_to_reap()
            # reap results
            results = combos_reap(crop)

        assert results == expected

    def test_field_name_and_overlapping(self):
        combos1 = [
            ('a', [10, 20, 30]),
            ('b', [4, 5, 6, 7]),
        ]
        expected1 = combo_runner(foo_add, combos1, constants={'c': True})

        combos2 = [
            ('a', [40, 50, 60]),
            ('b', [4, 5, 6, 7]),
        ]
        expected2 = combo_runner(foo_add, combos2, constants={'c': True})

        with TemporaryDirectory() as tdir:
            # sow seeds
            c1 = Crop(name='run1', fn=foo_add, parent_dir=tdir, batchsize=5)
            combos_sow(c1, combos1, constants={'c': True})
            c2 = Crop(name='run2', fn=foo_add, parent_dir=tdir, batchsize=5)
            combos_sow(c2, combos2, constants={'c': True})

            # grow seeds
            for i in range(1, 4):
                grow(i, Crop(parent_dir=tdir, name='run1'))
                grow(i, Crop(parent_dir=tdir, name='run2'))

            # reap results
            results1 = combos_reap(c1)
            results2 = combos_reap(c2)

        assert results1 == expected1
        assert results2 == expected2

    def test_combo_reaper_to_ds(self):
        combos = (('a', [1, 2]),
                  ('b', [10, 20, 30]),
                  ('c', [100, 200, 300, 400]))

        with TemporaryDirectory() as tdir:

            # sow seeds
            crop = Crop(fn=foo3_scalar, parent_dir=tdir, batchsize=5)
            combos_sow(crop, combos)

            # grow seeds
            for i in range(1, 6):
                grow(i, Crop(parent_dir=tdir, name='foo3_scalar'))

            ds = combos_reap_to_ds(crop, var_names=['bananas'])

        assert ds.sel(a=2, b=30, c=400)['bananas'].data == 432