# pylint: disable=W0223

from datetime import datetime
from pandas.core.common import _asarray_tuplesafe, is_list_like
from pandas.core.index import Index, MultiIndex, _ensure_index
from pandas.compat import range, zip
import pandas.compat as compat
import pandas.core.common as com
from pandas.core.common import (_is_bool_indexer,
                                ABCSeries, ABCDataFrame, ABCPanel)
import pandas.lib as lib

import numpy as np

# the supported indexers
def get_indexers_list():

    return [
        ('ix'  ,_NDFrameIndexer),
        ('iloc',_iLocIndexer   ),
        ('loc' ,_LocIndexer    ),
        ('at'  ,_AtIndexer     ),
        ('iat' ,_iAtIndexer    ),
        ]

# "null slice"
_NS = slice(None, None)


class IndexingError(Exception):
    pass


class _NDFrameIndexer(object):
    _exception   = KeyError

    def __init__(self, obj, name):
        self.obj = obj
        self.ndim = obj.ndim
        self.name = name

    def __iter__(self):
        raise NotImplementedError('ix is not iterable')

    def __getitem__(self, key):
        if type(key) is tuple:
            try:
                return self.obj.get_value(*key)
            except Exception:
                pass

            return self._getitem_tuple(key)
        else:
            return self._getitem_axis(key, axis=0)

    def _get_label(self, label, axis=0):
        # ueber-hack
        if (isinstance(label, tuple) and
                isinstance(label[axis], slice)):

            raise IndexingError('no slices here')

        try:
            return self.obj._xs(label, axis=axis, copy=False)
        except Exception:
            return self.obj._xs(label, axis=axis, copy=True)

    def _get_loc(self, key, axis=0):
        return self.obj._ixs(key, axis=axis)

    def _slice(self, obj, axis=0, raise_on_error=False):
        return self.obj._slice(obj, axis=axis, raise_on_error=raise_on_error)

    def __setitem__(self, key, value):
        # kludgetastic
        ax = self.obj._get_axis(0)
        if isinstance(ax, MultiIndex):
            try:
                indexer = ax.get_loc(key)
                self._setitem_with_indexer(indexer, value)
                return
            except Exception:
                pass

        if isinstance(key, tuple):
            if len(key) > self.ndim:
                raise IndexingError('only tuples of length <= %d supported' %
                                    self.ndim)
            indexer = self._convert_tuple(key, is_setter=True)
        else:
            indexer = self._convert_to_indexer(key, is_setter=True)

        self._setitem_with_indexer(indexer, value)

    def _has_valid_tuple(self, key):
        pass

    def _convert_tuple(self, key, is_setter=False):
        keyidx = []
        for i, k in enumerate(key):
            idx = self._convert_to_indexer(k, axis=i, is_setter=is_setter)
            keyidx.append(idx)
        return tuple(keyidx)

    def _has_valid_setitem_indexer(self, indexer):
        return True

    def _has_valid_positional_setitem_indexer(self, indexer):
        """ validate that an positional indexer cannot enlarge its target
            will raise if needed, does not modify the indexer externally """
        if isinstance(indexer, dict):
            raise IndexError("{0} cannot enlarge its target object".format(self.name))
        else:
            if not isinstance(indexer, tuple):
                indexer = self._tuplify(indexer)
            for ax, i in zip(self.obj.axes,indexer):
                if isinstance(i, slice):
                    # should check the stop slice?
                    pass
                elif is_list_like(i):
                    # should check the elements?
                    pass
                elif com.is_integer(i):
                    if i >= len(ax):
                        raise IndexError("{0} cannot enlarge its target object".format(self.name))
                elif isinstance(i, dict):
                    raise IndexError("{0} cannot enlarge its target object".format(self.name))

        return True

    def _setitem_with_indexer(self, indexer, value):

        self._has_valid_setitem_indexer(indexer)

        # also has the side effect of consolidating in-place
        from pandas import Panel, DataFrame, Series

        # maybe partial set
        take_split_path = self.obj._is_mixed_type
        if isinstance(indexer,tuple):
            nindexer = []
            for i, idx in enumerate(indexer):
                if isinstance(idx, dict):

                    # reindex the axis to the new value
                    # and set inplace
                    key,_ = _convert_missing_indexer(idx)

                    # if this is the items axes, then take the main missing path
                    # first; this correctly sets the dtype and avoids cache issues
                    # essentially this separates out the block that is needed to possibly
                    # be modified
                    if self.ndim > 1 and i == self.obj._info_axis_number:

                        # add the new item, and set the value
                        new_indexer = _convert_from_missing_indexer_tuple(indexer)
                        self.obj[key] = np.nan
                        self.obj.loc[new_indexer] = value
                        return self.obj

                    # reindex the axis
                    index = self.obj._get_axis(i)
                    labels = _safe_append_to_index(index, key)
                    self.obj._data = self.obj.reindex_axis(labels,i)._data

                    if isinstance(labels,MultiIndex):
                        self.obj.sortlevel(inplace=True)
                        labels = self.obj._get_axis(i)

                    nindexer.append(labels.get_loc(key))

                else:
                    nindexer.append(idx)

            indexer = tuple(nindexer)
        else:

            indexer, missing = _convert_missing_indexer(indexer)

            if missing:

                # reindex the axis to the new value
                # and set inplace
                if self.ndim == 1:
                    index = self.obj.index
                    if len(index) == 0:
                        new_index = Index([indexer])
                    else:
                        new_index = _safe_append_to_index(index, indexer)

                    new_values = np.concatenate([self.obj.values, [value]])
                    self.obj._data = self.obj._constructor(new_values, index=new_index, name=self.obj.name)
                    self.obj._maybe_update_cacher(clear=True)
                    return self.obj

                elif self.ndim == 2:
                    index = self.obj._get_axis(0)
                    labels = _safe_append_to_index(index, indexer)
                    self.obj._data = self.obj.reindex_axis(labels,0)._data
                    self.obj._maybe_update_cacher(clear=True)
                    return getattr(self.obj,self.name).__setitem__(indexer,value)

                # set using setitem (Panel and > dims)
                elif self.ndim >= 3:
                    return self.obj.__setitem__(indexer,value)

        # set
        info_axis = self.obj._info_axis_number
        item_labels = self.obj._get_axis(info_axis)

        # if we have a complicated setup, take the split path
        if isinstance(indexer, tuple) and any([ isinstance(ax,MultiIndex) for ax in self.obj.axes ]):
            take_split_path = True

        # align and set the values
        if take_split_path:

            if not isinstance(indexer, tuple):
                indexer = self._tuplify(indexer)

            if isinstance(value, ABCSeries):
                value = self._align_series(indexer, value)

            info_idx = indexer[info_axis]
            if com.is_integer(info_idx):
                info_idx = [info_idx]
            labels = item_labels[info_idx]

            # if we have a partial multiindex, then need to adjust the plane indexer here
            if len(labels) == 1 and isinstance(self.obj[labels[0]].index,MultiIndex):
                index = self.obj[labels[0]].index
                idx = indexer[:info_axis][0]
                try:
                    if idx in index:
                        idx = index.get_loc(idx)
                except:
                    pass
                plane_indexer = tuple([idx]) + indexer[info_axis + 1:]
                lplane_indexer = _length_of_indexer(plane_indexer[0],index)

                if is_list_like(value) and lplane_indexer != len(value):
                    raise ValueError("cannot set using a multi-index selection indexer with a different length than the value")

            # non-mi
            else:
                plane_indexer = indexer[:info_axis] + indexer[info_axis + 1:]
                if info_axis > 0:
                    plane_axis = self.obj.axes[:info_axis][0]
                    lplane_indexer = _length_of_indexer(plane_indexer[0],plane_axis)
                else:
                    lplane_indexer = 0

            def setter(item, v):
                s = self.obj[item]
                pi = plane_indexer[0] if lplane_indexer == 1 else plane_indexer

                # set the item, possibly having a dtype change
                s = s.copy()
                s._data = s._data.setitem(pi,v)
                s._maybe_update_cacher(clear=True)
                self.obj[item] = s

            def can_do_equal_len():
                """ return True if we have an equal len settable """
                if not len(labels) == 1:
                    return False

                l = len(value)
                item = labels[0]
                index = self.obj[item].index

                # equal len list/ndarray
                if len(index) == l:
                    return True
                elif lplane_indexer == l:
                    return True

                return False

            if _is_list_like(value):

                # we have an equal len Frame
                if isinstance(value, ABCDataFrame) and value.ndim > 1:

                    for item in labels:

                        # align to
                        if item in value:
                            v = value[item]
                            v = v.reindex(self.obj[item].index & v.index)
                            setter(item, v.values)
                        else:
                            setter(item, np.nan)

                # we have an equal len ndarray to our labels
                elif isinstance(value, np.ndarray) and value.ndim == 2:
                    if len(labels) != value.shape[1]:
                        raise ValueError('Must have equal len keys and value when'
                                         ' setting with an ndarray')

                    for i, item in enumerate(labels):
                        setter(item, value[:,i])

                # we have an equal len list/ndarray
                elif can_do_equal_len():
                    setter(labels[0], value)

                # per label values
                else:

                    for item, v in zip(labels, value):
                        setter(item, v)
            else:

                # scalar
                for item in labels:
                    setter(item, value)

        else:
            if isinstance(indexer, tuple):
                indexer = _maybe_convert_ix(*indexer)

            if isinstance(value, ABCSeries):
                value = self._align_series(indexer, value)

            elif isinstance(value, ABCDataFrame):
                value = self._align_frame(indexer, value)

            if isinstance(value, ABCPanel):
                value = self._align_panel(indexer, value)

            self.obj._data = self.obj._data.setitem(indexer,value)
            self.obj._maybe_update_cacher(clear=True)

    def _align_series(self, indexer, ser):
        # indexer to assign Series can be tuple or scalar
        if isinstance(indexer, tuple):

            aligners = [ not _is_null_slice(idx) for idx in indexer ]
            single_aligner = sum(aligners) == 1
            is_frame = self.obj.ndim == 2
            is_panel = self.obj.ndim >= 3

            # are we a single alignable value on a non-primary
            # dim (e.g. panel: 1,2, or frame: 0) ?
            # hence need to align to a single axis dimension
            # rather that find all valid dims

            # frame
            if is_frame:
                single_aligner = single_aligner and aligners[0]

            # panel
            elif is_panel:
                single_aligner = single_aligner and (aligners[1] or aligners[2])

            obj = self.obj
            for i, idx in enumerate(indexer):
                ax = obj.axes[i]

                # multiple aligners (or null slices)
                if com._is_sequence(idx) or isinstance(idx, slice):
                    if single_aligner and _is_null_slice(idx):
                        continue
                    new_ix = ax[idx]
                    if not is_list_like(new_ix):
                        new_ix = Index([new_ix])
                    if ser.index.equals(new_ix):
                        return ser.values.copy()
                    return ser.reindex(new_ix).values

                # 2 dims
                elif single_aligner and is_frame:

                    # reindex along index
                    ax = self.obj.axes[1]
                    if ser.index.equals(ax):
                        return ser.values.copy()
                    return ser.reindex(ax).values

                # >2 dims
                elif single_aligner:

                    broadcast = []
                    for n, labels in enumerate(self.obj._get_plane_axes(i)):

                        # reindex along the matching dimensions
                        if len(labels & ser.index):
                            ser = ser.reindex(labels)
                        else:
                            broadcast.append((n,len(labels)))

                    # broadcast along other dims
                    ser = ser.values.copy()
                    for (axis,l) in broadcast:
                        shape = [ -1 ] * (len(broadcast)+1)
                        shape[axis] = l
                        ser = np.tile(ser,l).reshape(shape)

                    if self.obj.ndim == 3:
                        ser = ser.T

                    return ser

        elif np.isscalar(indexer):
            ax = self.obj._get_axis(1)

            if ser.index.equals(ax):
                return ser.values.copy()

            return ser.reindex(ax).values

        raise ValueError('Incompatible indexer with Series')

    def _align_frame(self, indexer, df):
        is_frame = self.obj.ndim == 2
        is_panel = self.obj.ndim >= 3
        if isinstance(indexer, tuple):
            idx, cols = None, None
            sindexers = []
            for i, ix in enumerate(indexer):
                ax = self.obj.axes[i]
                if com._is_sequence(ix) or isinstance(ix, slice):
                    if idx is None:
                        idx = ax[ix].ravel()
                    elif cols is None:
                        cols = ax[ix].ravel()
                    else:
                        break
                else:
                    sindexers.append(i)

            # panel
            if is_panel:
                if len(sindexers) == 1 and idx is None and cols is None:
                    if sindexers[0] == 0:
                        df = df.T
                    return self.obj.conform(df,axis=sindexers[0])
                df = df.T

            if idx is not None and cols is not None:
                if df.index.equals(idx) and df.columns.equals(cols):
                    val = df.copy().values
                else:
                    val = df.reindex(idx, columns=cols).values
                return val

        elif ((isinstance(indexer, slice) or com.is_list_like(indexer))
              and is_frame):
            ax = self.obj.index[indexer]
            if df.index.equals(ax):
                val = df.copy().values
            else:
                val = df.reindex(ax).values
            return val

        elif np.isscalar(indexer) and not is_frame:
            idx = self.obj.axes[1]
            cols = self.obj.axes[2]

            # by definition we are indexing on the 0th axis
            if is_panel:
                df = df.T

            if idx.equals(df.index) and cols.equals(df.columns):
                return df.copy().values

            # a passed in dataframe which is actually a transpose
            # of what is needed
            elif idx.equals(df.columns) and cols.equals(df.index):
                return df.T.copy().values

            return df.reindex(idx, columns=cols).values

        raise ValueError('Incompatible indexer with DataFrame')

    def _align_panel(self, indexer, df):
        is_frame = self.obj.ndim == 2
        is_panel = self.obj.ndim >= 3
        raise NotImplementedError("cannot set using an indexer with a Panel yet!")

    def _getitem_tuple(self, tup):
        try:
            return self._getitem_lowerdim(tup)
        except IndexingError:
            pass

        # no multi-index, so validate all of the indexers
        self._has_valid_tuple(tup)

        # ugly hack for GH #836
        if self._multi_take_opportunity(tup):
            return self._multi_take(tup)

        # no shortcut needed
        retval = self.obj
        for i, key in enumerate(tup):
            if i >= self.obj.ndim:
                raise IndexingError('Too many indexers')

            if _is_null_slice(key):
                continue

            retval = getattr(retval,self.name)._getitem_axis(key, axis=i)

        return retval

    def _multi_take_opportunity(self, tup):
        from pandas.core.generic import NDFrame

        # ugly hack for GH #836
        if not isinstance(self.obj, NDFrame):
            return False

        if not all(_is_list_like(x) for x in tup):
            return False

        # just too complicated
        for ax in self.obj._data.axes:
            if isinstance(ax, MultiIndex):
                return False

        return True

    def _multi_take(self, tup):
        """ create the reindex map for our objects, raise the _exception if we can't create the indexer """

        try:
            o = self.obj
            d = dict([ (a,self._convert_for_reindex(t, axis=o._get_axis_number(a))) for t, a in zip(tup, o._AXIS_ORDERS) ])
            return o.reindex(**d)
        except:
            raise self._exception

    def _convert_for_reindex(self, key, axis=0):
        labels = self.obj._get_axis(axis)

        if com._is_bool_indexer(key):
            key = _check_bool_indexer(labels, key)
            return labels[key]
        else:
            if isinstance(key, Index):
                # want Index objects to pass through untouched
                keyarr = key
            else:
                # asarray can be unsafe, NumPy strings are weird
                keyarr = _asarray_tuplesafe(key)

            if _is_integer_dtype(keyarr) and not _is_integer_index(labels):
                keyarr = com._ensure_platform_int(keyarr)
                return labels.take(keyarr)

            return keyarr

    def _getitem_lowerdim(self, tup):

        ax0 = self.obj._get_axis(0)
        # a bit kludgy
        if isinstance(ax0, MultiIndex):
            try:
                return self._get_label(tup, axis=0)
            except TypeError:
                # slices are unhashable
                pass
            except Exception as e1:
                if isinstance(tup[0], (slice, Index)):
                    raise IndexingError

                # raise the error if we are not sorted
                if not ax0.is_lexsorted_for_tuple(tup):
                    raise e1
                try:
                    loc = ax0.get_loc(tup[0])
                except KeyError:
                    raise e1

        if len(tup) > self.obj.ndim:
            raise IndexingError

        # to avoid wasted computation
        # df.ix[d1:d2, 0] -> columns first (True)
        # df.ix[0, ['C', 'B', A']] -> rows first (False)
        for i, key in enumerate(tup):
            if _is_label_like(key) or isinstance(key, tuple):
                section = self._getitem_axis(key, axis=i)

                # we have yielded a scalar ?
                if not _is_list_like(section):
                    return section

                # might have been a MultiIndex
                elif section.ndim == self.ndim:
                    new_key = tup[:i] + (_NS,) + tup[i + 1:]
                    # new_key = tup[:i] + tup[i+1:]
                else:
                    new_key = tup[:i] + tup[i + 1:]

                    # unfortunately need an odious kludge here because of
                    # DataFrame transposing convention
                    if (isinstance(section, ABCDataFrame) and i > 0
                            and len(new_key) == 2):
                        a, b = new_key
                        new_key = b, a

                    if len(new_key) == 1:
                        new_key, = new_key

                return getattr(section,self.name)[new_key]

        raise IndexingError('not applicable')

    def _getitem_axis(self, key, axis=0):
        labels = self.obj._get_axis(axis)
        if isinstance(key, slice):
            return self._get_slice_axis(key, axis=axis)
        elif _is_list_like(key) and not (isinstance(key, tuple) and
                                         isinstance(labels, MultiIndex)):

            if hasattr(key, 'ndim') and key.ndim > 1:
                raise ValueError('Cannot index with multidimensional key')

            return self._getitem_iterable(key, axis=axis)
        else:
            if com.is_integer(key):
                if axis == 0 and isinstance(labels, MultiIndex):
                    try:
                        return self._get_label(key, axis=axis)
                    except (KeyError, TypeError):
                        if _is_integer_index(self.obj.index.levels[0]):
                            raise

                if not _is_integer_index(labels):
                    return self._get_loc(key, axis=axis)

            return self._get_label(key, axis=axis)

    def _getitem_iterable(self, key, axis=0):
        labels = self.obj._get_axis(axis)

        def _reindex(keys, level=None):
            try:
                return self.obj.reindex_axis(keys, axis=axis, level=level)
            except AttributeError:
                # Series
                if axis != 0:
                    raise AssertionError('axis must be 0')
                return self.obj.reindex(keys, level=level)

        if com._is_bool_indexer(key):
            key = _check_bool_indexer(labels, key)
            inds, = key.nonzero()
            return self.obj.take(inds, axis=axis, convert=False)
        else:
            if isinstance(key, Index):
                # want Index objects to pass through untouched
                keyarr = key
            else:
                # asarray can be unsafe, NumPy strings are weird
                keyarr = _asarray_tuplesafe(key)

            if _is_integer_dtype(keyarr):
                if labels.inferred_type != 'integer':
                    keyarr = np.where(keyarr < 0,
                                      len(labels) + keyarr, keyarr)

                if labels.inferred_type == 'mixed-integer':
                    indexer = labels.get_indexer(keyarr)
                    if (indexer >= 0).all():
                        self.obj.take(indexer, axis=axis, convert=True)
                    else:
                        return self.obj.take(keyarr, axis=axis)
                elif not labels.inferred_type == 'integer':

                    return self.obj.take(keyarr, axis=axis)

            # this is not the most robust, but...
            if (isinstance(labels, MultiIndex) and
                    not isinstance(keyarr[0], tuple)):
                level = 0
            else:
                level = None

            keyarr_is_unique = Index(keyarr).is_unique

            # existing labels are unique and indexer is unique
            if labels.is_unique and keyarr_is_unique:
                return _reindex(keyarr, level=level)

            else:
                indexer, missing = labels.get_indexer_non_unique(keyarr)
                check = indexer != -1
                result = self.obj.take(indexer[check], axis=axis, convert=False)

                # need to merge the result labels and the missing labels
                if len(missing):
                    l = np.arange(len(indexer))

                    missing = com._ensure_platform_int(missing)
                    missing_labels = keyarr.take(missing)
                    missing_indexer = com._ensure_int64(l[~check])
                    cur_labels = result._get_axis(axis).values
                    cur_indexer = com._ensure_int64(l[check])

                    new_labels = np.empty(tuple([len(indexer)]),dtype=object)
                    new_labels[cur_indexer]     = cur_labels
                    new_labels[missing_indexer] = missing_labels

                    # reindex with the specified axis
                    ndim = self.obj.ndim
                    if axis+1 > ndim:
                        raise AssertionError("invalid indexing error with non-unique index")

                    # a unique indexer
                    if keyarr_is_unique:
                        new_indexer = (Index(cur_indexer) + Index(missing_indexer)).values
                        new_indexer[missing_indexer] = -1

                    # we have a non_unique selector, need to use the original indexer here
                    else:

                        # need to retake to have the same size as the indexer
                        rindexer = indexer.values
                        rindexer[~check] = 0
                        result = self.obj.take(rindexer, axis=axis, convert=False)

                        # reset the new indexer to account for the new size
                        new_indexer = np.arange(len(result))
                        new_indexer[~check] = -1

                    result = result._reindex_with_indexers({ axis : [ new_labels, new_indexer ] }, copy=True, allow_dups=True)

                return result

    def _convert_to_indexer(self, obj, axis=0, is_setter=False):
        """
        Convert indexing key into something we can use to do actual fancy
        indexing on an ndarray

        Examples
        ix[:5] -> slice(0, 5)
        ix[[1,2,3]] -> [1,2,3]
        ix[['foo', 'bar', 'baz']] -> [i, j, k] (indices of foo, bar, baz)

        Going by Zen of Python?
        "In the face of ambiguity, refuse the temptation to guess."
        raise AmbiguousIndexError with integer labels?
        - No, prefer label-based indexing
        """
        labels = self.obj._get_axis(axis)
        is_int_index = _is_integer_index(labels)

        if com.is_integer(obj) and not is_int_index:
            return obj

        try:
            return labels.get_loc(obj)
        except (KeyError, TypeError):
            pass

        if isinstance(obj, slice):
            ltype = labels.inferred_type

            # in case of providing all floats, use label-based indexing
            float_slice = (labels.inferred_type == 'floating'
                           and _is_float_slice(obj))

            # floats that are within tolerance of int used as positions
            int_slice = _is_index_slice(obj)

            null_slice = obj.start is None and obj.stop is None

            # could have integers in the first level of the MultiIndex,
            # in which case we wouldn't want to do position-based slicing
            position_slice = (int_slice
                              and not ltype == 'integer'
                              and not isinstance(labels, MultiIndex)
                              and not float_slice)

            start, stop = obj.start, obj.stop

            # last ditch effort: if we are mixed and have integers
            try:
                if position_slice and 'mixed' in ltype:
                    if start is not None:
                        i = labels.get_loc(start)
                    if stop is not None:
                        j = labels.get_loc(stop)
                    position_slice = False
            except KeyError:
                if ltype == 'mixed-integer-float':
                    raise

            if null_slice or position_slice:
                indexer = obj
            else:
                try:
                    indexer = labels.slice_indexer(start, stop, obj.step)
                except Exception:
                    if _is_index_slice(obj):
                        if ltype == 'integer':
                            raise
                        indexer = obj
                    else:
                        raise

            return indexer

        elif _is_list_like(obj):
            if com._is_bool_indexer(obj):
                obj = _check_bool_indexer(labels, obj)
                inds, = obj.nonzero()
                return inds
            else:
                if isinstance(obj, Index):
                    objarr = obj.values
                else:
                    objarr = _asarray_tuplesafe(obj)

                # If have integer labels, defer to label-based indexing
                if _is_integer_dtype(objarr) and not is_int_index:
                    if labels.inferred_type != 'integer':
                        objarr = np.where(objarr < 0,
                                          len(labels) + objarr, objarr)
                    return objarr

                # this is not the most robust, but...
                if (isinstance(labels, MultiIndex) and
                        not isinstance(objarr[0], tuple)):
                    level = 0
                    _, indexer = labels.reindex(objarr, level=level)

                    check = labels.levels[0].get_indexer(objarr)
                else:
                    level = None

                    # unique index
                    if labels.is_unique:
                        indexer = check = labels.get_indexer(objarr)

                    # non-unique (dups)
                    else:
                        indexer, missing = labels.get_indexer_non_unique(objarr)
                        check = indexer

                mask = check == -1
                if mask.any():

                    # mi here
                    if isinstance(obj, tuple) and is_setter:
                        return { 'key' : obj }
                    raise KeyError('%s not in index' % objarr[mask])

                return indexer

        else:
            try:
                return labels.get_loc(obj)
            except (KeyError):

                # allow a not found key only if we are a setter
                if not is_list_like(obj) and is_setter:
                    return { 'key' : obj }
                raise

    def _tuplify(self, loc):
        tup = [slice(None, None) for _ in range(self.ndim)]
        tup[0] = loc
        return tuple(tup)

    def _get_slice_axis(self, slice_obj, axis=0):
        obj = self.obj

        if not _need_slice(slice_obj):
            return obj

        labels = obj._get_axis(axis)

        ltype = labels.inferred_type

        # in case of providing all floats, use label-based indexing
        float_slice = (labels.inferred_type == 'floating'
                       and _is_float_slice(slice_obj))

        # floats that are within tolerance of int used as positions
        int_slice = _is_index_slice(slice_obj)

        null_slice = slice_obj.start is None and slice_obj.stop is None

        # could have integers in the first level of the MultiIndex,
        # in which case we wouldn't want to do position-based slicing
        position_slice = (int_slice
                          and not ltype == 'integer'
                          and not isinstance(labels, MultiIndex)
                          and not float_slice)

        start, stop = slice_obj.start, slice_obj.stop

        # last ditch effort: if we are mixed and have integers
        try:
            if position_slice and 'mixed' in ltype:
                if start is not None:
                    i = labels.get_loc(start)
                if stop is not None:
                    j = labels.get_loc(stop)
                position_slice = False
        except KeyError:
            if ltype == 'mixed-integer-float':
                raise

        if null_slice or position_slice:
            indexer = slice_obj
        else:
            try:
                indexer = labels.slice_indexer(start, stop, slice_obj.step)
            except Exception:
                if _is_index_slice(slice_obj):
                    if ltype == 'integer':
                        raise
                    indexer = slice_obj
                else:
                    raise

        if isinstance(indexer, slice):
            return self._slice(indexer, axis=axis)
        else:
            return self.obj.take(indexer, axis=axis)

class _LocationIndexer(_NDFrameIndexer):
    _valid_types = None
    _exception   = Exception

    def _has_valid_type(self, k, axis):
        raise NotImplementedError()

    def _has_valid_tuple(self, key):
        """ check the key for valid keys across my indexer """
        for i, k in enumerate(key):
            if i >= self.obj.ndim:
                raise ValueError('Too many indexers')
            if not self._has_valid_type(k,i):
                raise ValueError("Location based indexing can only have [%s] types" % self._valid_types)

    def __getitem__(self, key):
        if type(key) is tuple:
            return self._getitem_tuple(key)
        else:
            return self._getitem_axis(key, axis=0)

    def _getitem_axis(self, key, axis=0):
        raise NotImplementedError()

    def _getbool_axis(self, key, axis=0):
            labels = self.obj._get_axis(axis)
            key = _check_bool_indexer(labels, key)
            inds, = key.nonzero()
            try:
                return self.obj.take(inds, axis=axis, convert=False)
            except (Exception) as detail:
                raise self._exception(detail)
    def _get_slice_axis(self, slice_obj, axis=0):
        """ this is pretty simple as we just have to deal with labels """
        obj = self.obj
        if not _need_slice(slice_obj):
            return obj

        labels = obj._get_axis(axis)
        indexer = labels.slice_indexer(slice_obj.start, slice_obj.stop, slice_obj.step)

        if isinstance(indexer, slice):
            return self._slice(indexer, axis=axis)
        else:
            return self.obj.take(indexer, axis=axis)

class _LocIndexer(_LocationIndexer):
    """ purely label based location based indexing """
    _valid_types = "labels (MUST BE IN THE INDEX), slices of labels (BOTH endpoints included! Can be slices of integers if the index is integers), listlike of labels, boolean"
    _exception   = KeyError

    def _has_valid_type(self, key, axis):
        ax = self.obj._get_axis(axis)

        # valid for a label where all labels are in the index
        # slice of lables (where start-end in labels)
        # slice of integers (only if in the lables)
        # boolean

        if isinstance(key, slice):

            if key.start is not None:
                if key.start not in ax:
                    raise KeyError("start bound [%s] is not the [%s]" % (key.start,self.obj._get_axis_name(axis)))
            if key.stop is not None:
                if key.stop not in ax:
                    raise KeyError("stop bound [%s] is not in the [%s]" % (key.stop,self.obj._get_axis_name(axis)))

        elif com._is_bool_indexer(key):
                return True

        elif _is_list_like(key):

            # require all elements in the index
            idx = _ensure_index(key)
            if not idx.isin(ax).all():
                raise KeyError("[%s] are not in ALL in the [%s]" % (key,self.obj._get_axis_name(axis)))

            return True

        else:

            # if its empty we want a KeyError here
            if not len(ax):
                raise KeyError("The [%s] axis is empty" % self.obj._get_axis_name(axis))

            try:
                if not key in ax:
                    raise KeyError("the label [%s] is not in the [%s]" % (key,self.obj._get_axis_name(axis)))
            except (TypeError):

                # if we have a weird type of key/ax
                raise KeyError("the label [%s] is not in the [%s]" % (key,self.obj._get_axis_name(axis)))


        return True

    def _getitem_axis(self, key, axis=0):
        labels = self.obj._get_axis(axis)

        if isinstance(key, slice):
            self._has_valid_type(key,axis)
            return self._get_slice_axis(key, axis=axis)
        elif com._is_bool_indexer(key):
            return self._getbool_axis(key, axis=axis)
        elif _is_list_like(key) and not (isinstance(key, tuple) and
                                         isinstance(labels, MultiIndex)):

            if hasattr(key, 'ndim') and key.ndim > 1:
                raise ValueError('Cannot index with multidimensional key')

            return self._getitem_iterable(key, axis=axis)
        else:
            return self._get_label(key, axis=axis)

class _iLocIndexer(_LocationIndexer):
    """ purely integer based location based indexing """
    _valid_types = "integer, integer slice (START point is INCLUDED, END point is EXCLUDED), listlike of integers, boolean array"
    _exception   = IndexError

    def _has_valid_type(self, key, axis):
        if com._is_bool_indexer(key):
            if hasattr(key,'index') and isinstance(key.index,Index):
                if key.index.inferred_type == 'integer':
                    raise NotImplementedError("iLocation based boolean indexing on an integer type is not available")
                raise ValueError("iLocation based boolean indexing cannot use an indexable as a mask")
            return True

        return isinstance(key, slice) or com.is_integer(key) or _is_list_like(key)

    def _has_valid_setitem_indexer(self, indexer):
        self._has_valid_positional_setitem_indexer(indexer)

    def _getitem_tuple(self, tup):

        self._has_valid_tuple(tup)
        try:
            return self._getitem_lowerdim(tup)
        except:
            pass

        retval = self.obj
        for i, key in enumerate(tup):
            if i >= self.obj.ndim:
                raise IndexingError('Too many indexers')

            if _is_null_slice(key):
                continue

            retval = getattr(retval,self.name)._getitem_axis(key, axis=i)

        return retval

    def _get_slice_axis(self, slice_obj, axis=0):
        obj = self.obj

        if not _need_slice(slice_obj):
            return obj

        if isinstance(slice_obj, slice):
            return self._slice(slice_obj, axis=axis, raise_on_error=True)
        else:
            return self.obj.take(slice_obj, axis=axis)

    def _getitem_axis(self, key, axis=0):
        if isinstance(key, slice):
            self._has_valid_type(key,axis)
            return self._get_slice_axis(key, axis=axis)

        elif com._is_bool_indexer(key):
            self._has_valid_type(key,axis)
            return self._getbool_axis(key, axis=axis)

        # a single integer or a list of integers
        else:

            if not (com.is_integer(key) or _is_list_like(key)):
                raise ValueError("Cannot index by location index with a non-integer key")

            return self._get_loc(key,axis=axis)

    def _convert_to_indexer(self, obj, axis=0, is_setter=False):
        """ much simpler as we only have to deal with our valid types """
        if self._has_valid_type(obj,axis):
            return obj

        raise ValueError("Can only index by location with a [%s]" % self._valid_types)


class _ScalarAccessIndexer(_NDFrameIndexer):
    """ access scalars quickly """

    def _convert_key(self, key):
        return list(key)

    def __getitem__(self, key):
        if not isinstance(key, tuple):

            # we could have a convertible item here (e.g. Timestamp)
            if not _is_list_like(key):
                key = tuple([ key ])
            else:
                raise ValueError('Invalid call for scalar access (getting)!')

        key = self._convert_key(key)
        return self.obj.get_value(*key)

    def __setitem__(self, key, value):
        if not isinstance(key, tuple):
            key = self._tuplify(key)
        if len(key) != self.obj.ndim:
            raise ValueError('Not enough indexers for scalar access (setting)!')
        key = self._convert_key(key)
        key.append(value)
        self.obj.set_value(*key)

class _AtIndexer(_ScalarAccessIndexer):
    """ label based scalar accessor """
    pass

class _iAtIndexer(_ScalarAccessIndexer):
    """ integer based scalar accessor """

    def _has_valid_setitem_indexer(self, indexer):
        self._has_valid_positional_setitem_indexer(indexer)

    def _convert_key(self, key):
        """ require  integer args (and convert to label arguments) """
        ckey = []
        for a, i in zip(self.obj.axes,key):
            if not com.is_integer(i):
                raise ValueError("iAt based indexing can only have integer indexers")
            ckey.append(a[i])
        return ckey

# 32-bit floating point machine epsilon
_eps = np.finfo('f4').eps

def _length_of_indexer(indexer,target=None):
    """ return the length of a single non-tuple indexer which could be a slice """
    if target is not None and isinstance(indexer, slice):
        l = len(target)
        start = indexer.start
        stop = indexer.stop
        step = indexer.step
        if start is None:
            start = 0
        elif start < 0:
            start += l
        if stop is None or stop > l:
            stop = l
        elif stop < 0:
            stop += l
        if step is None:
            step = 1
        elif step < 0:
            step = abs(step)
        return (stop-start) / step
    elif isinstance(indexer, (ABCSeries, np.ndarray, list)):
        return len(indexer)
    elif not is_list_like(indexer):
        return 1
    raise AssertionError("cannot find the length of the indexer")

def _convert_to_index_sliceable(obj, key):
    """ if we are index sliceable, then return my slicer, otherwise return None """
    idx = obj.index
    if isinstance(key, slice):
        idx_type = idx.inferred_type
        if idx_type == 'floating':
            indexer = obj.ix._convert_to_indexer(key, axis=0)
        elif idx_type == 'integer' or _is_index_slice(key):
            indexer = key
        else:
            indexer = obj.ix._convert_to_indexer(key, axis=0)
        return indexer

    elif isinstance(key, compat.string_types):

        # we are an actual column
        if key in obj._data.items:
            return None

        # we need a timelike key here
        if idx.is_all_dates:
            try:
                return idx._get_string_slice(key)
            except:
                return None

    return None

def _is_index_slice(obj):
    def _is_valid_index(x):
        return (com.is_integer(x) or com.is_float(x)
                and np.allclose(x, int(x), rtol=_eps, atol=0))

    def _crit(v):
        return v is None or _is_valid_index(v)

    both_none = obj.start is None and obj.stop is None

    return not both_none and (_crit(obj.start) and _crit(obj.stop))


def _is_int_slice(obj):
    def _is_valid_index(x):
        return com.is_integer(x)

    def _crit(v):
        return v is None or _is_valid_index(v)

    both_none = obj.start is None and obj.stop is None

    return not both_none and (_crit(obj.start) and _crit(obj.stop))


def _is_float_slice(obj):
    def _is_valid_index(x):
        return com.is_float(x)

    def _crit(v):
        return v is None or _is_valid_index(v)

    both_none = obj.start is None and obj.stop is None

    return not both_none and (_crit(obj.start) and _crit(obj.stop))


class _SeriesIndexer(_NDFrameIndexer):
    """
    Class to support fancy indexing, potentially using labels

    Notes
    -----
    Indexing based on labels is INCLUSIVE
    Slicing uses PYTHON SEMANTICS (endpoint is excluded)

    If Index contains int labels, these will be used rather than the locations,
    so be very careful (ambiguous).

    Examples
    --------
    >>> ts.ix[5:10] # equivalent to ts[5:10]
    >>> ts.ix[[date1, date2, date3]]
    >>> ts.ix[date1:date2] = 0
    """

    def _get_label(self, key, axis=0):
        return self.obj[key]

    def _get_loc(self, key, axis=0):
        return self.obj.values[key]

    def _slice(self, indexer, axis=0):
        return self.obj._get_values(indexer)

    def _setitem_with_indexer(self, indexer, value):

        # need to delegate to the super setter
        if isinstance(indexer, dict):
            return super(_SeriesIndexer, self)._setitem_with_indexer(indexer, value)

        # fast access
        self.obj._set_values(indexer, value)

def _check_bool_indexer(ax, key):
    # boolean indexing, need to check that the data are aligned, otherwise
    # disallowed

    # this function assumes that com._is_bool_indexer(key) == True

    result = key
    if isinstance(key, ABCSeries) and not key.index.equals(ax):
        result = result.reindex(ax)
        mask = com.isnull(result.values)
        if mask.any():
            raise IndexingError('Unalignable boolean Series key provided')

        result = result.astype(bool).values

    else:
        # com._is_bool_indexer has already checked for nulls in the case of an
        # object array key, so no check needed here
        result = np.asarray(result, dtype=bool)

    return result


def _convert_missing_indexer(indexer):
    """ reverse convert a missing indexer, which is a dict
        return the scalar indexer and a boolean indicating if we converted """

    if isinstance(indexer, dict):

        # a missing key (but not a tuple indexer)
        indexer = indexer['key']

        if isinstance(indexer, bool):
            raise KeyError("cannot use a single bool to index into setitem")
        return indexer, True

    return indexer, False

def _convert_from_missing_indexer_tuple(indexer):
    """ create a filtered indexer that doesn't have any missing indexers """
    def get_indexer(_idx):
        return _idx['key'] if isinstance(_idx,dict) else _idx
    return tuple([ get_indexer(_idx) for _i, _idx in enumerate(indexer) ])

def _safe_append_to_index(index, key):
    """ a safe append to an index, if incorrect type, then catch and recreate """
    try:
        return index.insert(len(index), key)
    except:
        return Index(np.concatenate([index.asobject.values,np.array([key])]))

def _maybe_convert_indices(indices, n):
    """ if we have negative indicies, translate to postive here
        if have indicies that are out-of-bounds, raise an IndexError """
    if isinstance(indices, list):
        indices = np.array(indices)

    mask = indices<0
    if mask.any():
        indices[mask] += n
    mask = (indices>=n) | (indices<0)
    if mask.any():
        raise IndexError("indices are out-of-bounds")
    return indices

def _maybe_convert_ix(*args):
    """
    We likely want to take the cross-product
    """

    ixify = True
    for arg in args:
        if not isinstance(arg, (np.ndarray, list, ABCSeries)):
            ixify = False

    if ixify:
        return np.ix_(*args)
    else:
        return args


def _is_null_slice(obj):
    return (isinstance(obj, slice) and obj.start is None and
            obj.stop is None and obj.step is None)


def _is_integer_dtype(arr):
    return (issubclass(arr.dtype.type, np.integer) and
            not arr.dtype.type == np.datetime64)


def _is_integer_index(index):
    return index.inferred_type == 'integer'


def _is_label_like(key):
    # select a label or row
    return not isinstance(key, slice) and not _is_list_like(key)


def _is_list_like(obj):
    # Consider namedtuples to be not list like as they are useful as indices
    return (np.iterable(obj)
            and not isinstance(obj, compat.string_types)
            and not (isinstance(obj, tuple) and type(obj) is not tuple))


def _need_slice(obj):
    return (obj.start is not None or
            obj.stop is not None or
            (obj.step is not None and obj.step != 1))


def _check_slice_bounds(slobj, values):
    l = len(values)
    start = slobj.start
    if start is not None:
        if start < -l or start > l-1:
            raise IndexError("out-of-bounds on slice (start)")
    stop = slobj.stop
    if stop is not None:
        if stop < -l-1 or stop > l:
            raise IndexError("out-of-bounds on slice (end)")

def _maybe_droplevels(index, key):
    # drop levels
    original_index = index
    if isinstance(key, tuple):
        for _ in key:
            try:
                index = index.droplevel(0)
            except:
                # we have dropped too much, so back out
                return original_index
    else:
        index = index.droplevel(0)

    return index
