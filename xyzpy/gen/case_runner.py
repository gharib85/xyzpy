"""Functions for systematically evaluating a function over specific cases.
"""

import itertools
from cytoolz import concat
import numpy as np
import xarray as xr

from ..utils import _get_fn_name, progbar  # unzip
from .prepare import (
    _parse_fn_args,
    _parse_cases,
    _parse_case_results,
    _parse_var_names,
    _parse_var_dims,
    _parse_var_coords,
    _parse_constants,
    _parse_resources
)


from .combo_runner import _combo_runner


def _case_runner(fn, fn_args, cases, constants,
                 split=False,
                 parallel=False,
                 num_workers=None,
                 scheduler=None,
                 pool=None,
                 hide_progbar=False):
    """Core case runner, i.e. without parsing of arguments.
    """
    # Turn the function into a single arg function to send to combo_runner
    def single_dict_arg_fn(kws, **kwargs):
        return fn(**kws, **kwargs)

    fn_name = _get_fn_name(fn)
    single_dict_arg_fn.__name__ = fn_name
    combos = (('kws', [dict(zip(fn_args, case)) for case in cases]),)

    return _combo_runner(single_dict_arg_fn, combos,
                         constants=constants,
                         split=split,
                         parallel=parallel,
                         num_workers=num_workers,
                         scheduler=scheduler,
                         pool=pool,
                         hide_progbar=hide_progbar)


def case_runner(fn, fn_args, cases,
                constants=None,
                split=False,
                parallel=False,
                scheduler=None,
                pool=None,
                num_workers=None,
                hide_progbar=False):
    """Evaluate a function in many different configurations, optionally in
    parallel and or with live progress.

    Parameters
    ----------
        fn : callable
            Function with which to evalute cases with
        fn_args : tuple
            Names of case arguments that fn takes
        cases : tuple of tuple
            List settings that fn_args take
        constants : dict (optional)
            List of tuples/dict of *constant* fn argument mappings.
        split : bool (optional)
            Whether to split into multiple output arrays or not.
        parallel : bool (optional)
            Process combos in parallel, default number of workers picked.
        scheduler : str or dask.get instance (optional)
            Specify scheduler to use for the parallel work.
        pool : executor-like pool (optional)
            Submit all combos to this pool.
        num_workers : int (optional)
            Explicitly choose how many workers to use, None for automatic.
        hide_progbar : bool (optional)
            Whether to disable the progress bar.

    Returns
    -------
        results : list of fn output for each case
    """
    # Prepare fn_args and values
    fn_args = _parse_fn_args(fn_args)
    cases = _parse_cases(cases)
    constants = _parse_constants(constants)

    return _case_runner(fn, fn_args, cases,
                        constants=constants,
                        split=split,
                        parallel=parallel,
                        scheduler=scheduler,
                        num_workers=num_workers,
                        pool=pool,
                        hide_progbar=hide_progbar)


def find_union_coords(cases):
    """Take a list of cases and find the union of coordinates
    with which to index all cases. Sort the coords if possible.
    """
    for x in zip(*cases):
        try:
            yield sorted(list(set(x)))
        except TypeError:  # unsortable
            yield list(set(x))


def all_missing_ds(coords, var_names, all_dims, var_types, attrs=None):
    """Make a dataset whose data is all missing.

    Parameters
    ----------
        coords : dict
            coordinates of dataset
        var_names : tuple
            names of each variable in dataset
        all_dims : tuple
            corresponding list of dimensions for each variable
        var_types : tuple
            corresponding list of types for each variable
    """
    # Blank dataset with appropirate coordinates
    ds = xr.Dataset(coords=coords, attrs=attrs)
    for v_name, v_dims, v_type in zip(var_names, all_dims, var_types):
        shape = tuple(ds[d].size for d in v_dims)
        if v_type == int or v_type == float:
            # Warn about upcasting int to float?
            nodata = np.tile(np.nan, shape)
        elif v_type == complex:
            nodata = np.tile(np.nan + np.nan * 1.0j, shape)
        else:
            nodata = np.tile(None, shape).astype(object)
        ds[v_name] = (v_dims, nodata)
    return ds


def _cases_to_ds(results, fn_args, cases, var_names, add_to_ds=None,
                 var_dims=None, var_coords=None, constants=None, attrs=None,
                 overwrite=False):
    """ Take a list of results and configurations that generate them and turn it
    into a `xarray.Dataset`.

    Parameters
    ----------
        results: list(s) of results of len(cases), e.g. generated by
            `case_runner`.
        fn_args: arguments used in function that generated the results
        cases: list of configurations used to generate results
        var_names: name(s) of output variables for a single result
        var_dims: the list of named coordinates for each single result
            variable, i.e. coordinates not generated by the combo_runner
        var_coords: dict of values for those coordinates if custom ones are
            desired.

    Returns
    -------
        ds: Dataset holding all results, with coordinates described by cases

    Notes
    -----
        1. Many data types have to be converted to object in order for the
            missing values to be represented by NaNs.
    """
    results = _parse_case_results(results, var_names)

    if add_to_ds:
        ds = add_to_ds
    else:
        # Find minimal covering set of coordinates for fn_args
        case_coords = dict(zip(fn_args, find_union_coords(cases)))

        # Create new, 'all missing' dataset if required
        ds = all_missing_ds(coords={**case_coords, **var_coords},
                            var_names=var_names, attrs=attrs,
                            all_dims=tuple(fn_args + var_dims[k]
                                           for k in var_names),
                            var_types=(np.asarray(x).dtype
                                       for x in results[0]))
        if constants:
            ds.attrs.update({k: v for k, v in constants.items()
                             if k not in ds.dims})

    # Go through cases, overwriting nan with results
    for res, cfg in zip(results, cases):
        for vname, x in zip(var_names, res):
            if not overwrite:
                if not ds[vname].loc[dict(zip(fn_args, cfg))].isnull().all():
                    raise ValueError("Existing data and `overwrite` = False")
            try:
                len(x)
                ds[vname].loc[dict(zip(fn_args, cfg))] = np.asarray(x)
            except TypeError:
                ds[vname].loc[dict(zip(fn_args, cfg))] = x

    return ds


def case_runner_to_ds(fn, fn_args, cases, var_names,
                      var_dims=None,
                      var_coords=None,
                      constants=None,
                      resources=None,
                      attrs=None,
                      add_to_ds=None,
                      overwrite=False,
                      parse=True,
                      **case_runner_settings):
    """ Combination of `case_runner` and `_cases_to_ds`. Takes a function and
    list of argument configurations and produces a `xarray.Dataset`.

    Parameters
    ----------
        fn: function to evaluate
        fn_args: names of function args
        cases: list of function arg configurations
        var_names: list of names of single fn output
        var_dims: list of list of extra dims for each fn output
        var_coords: dictionary describing custom values of var_dims
        case_runner_settings: dict to supply to `case_runner`

    Returns
    -------
        ds: dataset with minimal covering coordinates and all cases
            evaluated.
    """
    if parse:
        fn_args = _parse_fn_args(fn_args)
        cases = _parse_cases(cases)
        constants = _parse_constants(constants)
        resources = _parse_resources(resources)
        var_names = _parse_var_names(var_names)
        var_dims = _parse_var_dims(var_dims, var_names)
        var_coords = _parse_var_coords(var_coords)

    # Generate results
    results = _case_runner(fn, fn_args, cases,
                           constants={**constants, **resources},
                           **case_runner_settings)

    # Convert to xarray.Dataset
    ds = _cases_to_ds(results, fn_args, cases,
                      var_names=var_names,
                      var_dims=var_dims,
                      var_coords=var_coords,
                      constants=constants,
                      attrs=attrs,
                      add_to_ds=add_to_ds,
                      overwrite=overwrite)
    return ds


# --------------------------------------------------------------------------- #
# Update or add new values                                                    #
# --------------------------------------------------------------------------- #

def find_missing_cases(ds, ignore_dims=None, show_progbar=False):
    """Find all cases in a dataset with missing data.

    Parameters
    ----------
        ds : xarray.Dataset
            Dataset in which to find missing data
        ignore_dims : set (optional)
            internal variable dimensions (i.e. to ignore)
        show_progbar : bool (optional)
            Show the current progress

    Returns
    -------
        missing_fn_args, missing_cases :
            Function arguments and missing cases.
    """
    # Parse ignore_dims
    ignore_dims = ({ignore_dims} if isinstance(ignore_dims, str) else
                   set(ignore_dims) if ignore_dims else set())

    # Find all configurations
    fn_args = tuple(coo for coo in ds.dims if coo not in ignore_dims)
    var_names = tuple(ds.data_vars)
    all_cases = itertools.product(*(ds[arg].data for arg in fn_args))

    # Only return those corresponding to all missing data
    def gen_missing_list():
        for case in progbar(all_cases, disable=not show_progbar):
            sub_ds = ds.loc[dict(zip(fn_args, case))]
            if all(sub_ds[v].isnull().all() for v in var_names):
                yield case

    return fn_args, tuple(gen_missing_list())


def fill_missing_cases(ds, fn, var_names,
                       var_dims=None,
                       var_coords=None,
                       constants=None,
                       resources=None,
                       **case_runner_settings):
    """ Take a dataset and function etc. and fill its missing data in

    Parameters
    ----------
        ds : xarray.Dataset
            Dataset to analyse and fill
        fn : callable
            Function to use to fill missing cases
        var_names : tuple
            Output variable names of function
        var_dims : dict
            Output variabe named dimensions of function
        var_coords : dict
            Dictionary of coords for output dims
        **case_runner_settings: settings sent to `case_runner`

    Returns
    -------
        xarray.Dataset
    """
    var_names = _parse_var_names(var_names)
    var_dims = _parse_var_dims(var_dims, var_names)
    var_coords = _parse_var_coords(var_coords)
    constants = _parse_constants(constants)
    resources = _parse_resources(resources)

    # Gather all internal dimensions
    ignore_dims = set(concat(var_dims.values()))

    # Find missing cases
    fn_args, missing_cases = find_missing_cases(ds, ignore_dims=ignore_dims)

    # Generate missing results
    results = _case_runner(fn, fn_args, missing_cases,
                           constants={**constants, **resources},
                           **case_runner_settings)

    # Add to dataset
    return _cases_to_ds(results, fn_args, missing_cases,
                        var_names=var_names,
                        var_dims=var_dims,
                        var_coords=var_coords,
                        add_to_ds=ds)
