import os
import csv
import subprocess as sub

import luigi
import numpy as np
import pandas as pd

import test_params


class LuigiDataFile(luigi.Task):
    """Class to access files that already exist (no processing needed)."""
    data_fname = 'placeholder'

    def output(self):
        return luigi.LocalTarget(os.path.join('data', self.data_fname))


class StudentData(LuigiDataFile):
    data_fname = 'nsf_student.csv'

class AdmissionsData(LuigiDataFile):
    data_fname = 'nsf_admissions.csv'

class DegreesData(LuigiDataFile):
    data_fname = 'nsf_degrees.csv'

class CoursesData(LuigiDataFile):
    data_fname = 'nsf_courses.csv'


def fname_from_cname(cname):
    words = []
    chars = [cname[0]]
    for c in cname[1:]:
        if c.isupper():
            words.append(''.join(chars))
            chars = [c]
        else:
            chars.append(c)

    words.append(''.join(chars))
    return '-'.join(map(lambda s: s.lower(), words))


class BasicLuigiTask(luigi.Task):
    """Uses class name as output file."""
    ext = 'csv'

    def output(self):
        fname = fname_from_cname(self.__class__.__name__)
        return luigi.LocalTarget(os.path.join(
            'data', '%s.%s' % (fname, self.ext)))


class StudentIdMap(BasicLuigiTask):
    """Produce a contiguous id mapping for all students."""

    ids = ['id']

    def requires(self):
        return CoursesData()

    def run(self):
        with self.input().open() as f:
            courses = pd.read_csv(f)

        students = courses[self.ids].drop_duplicates().reset_index()[self.ids]
        with self.output().open('w') as f:
            students.to_csv(f, header='id', index_label='index', float_format='%.0f')


class CourseIdMap(BasicLuigiTask):
    """Produce a contiguous id mapping for all courses (DISC, CNUM, HRS)."""

    ids = ['DISC', 'CNUM', 'HRS']

    def requires(self):
        return CoursesData()

    def run(self):
        with self.input().open() as f:
            courses = pd.read_csv(f)

        # Get all unique courses, as specified by (DISC, CNUM, HRS).
        # we assume here that some courses have labs/recitations which
        # have the same (DISC, CNUM) but different number of HRS.
        triplets = courses[self.ids].drop_duplicates().reset_index()[self.ids]
        with self.output().open('w') as out:
            triplets.to_csv(out, index_label='index')


class InstructorIdMap(BasicLuigiTask):
    """Produce a contiguous id mapping for all instructors (LANME, FNAME)."""

    ids = ['INSTR_LNAME', 'INSTR_FNAME']

    def requires(self):
        return CoursesData()

    def run(self):
        with self.input().open() as f:
            courses = pd.read_csv(f)

        instr = courses[self.ids].drop_duplicates().reset_index()[self.ids]
        with self.output().open('w') as out:
            instr.to_csv(out, index_label='index', float_format='%.0f')


class OrdinalTermMap(BasicLuigiTask):
    """Produce an ordinal mapping (0...) for enrollment terms."""

    ids = ['TERMBNR']

    def requires(self):
        return CoursesData()

    def run(self):
        with self.input().open() as f:
            courses = pd.read_csv(f)

        terms = courses[self.ids].drop_duplicates().reset_index()[self.ids]
        with self.output().open('w') as out:
            terms.to_csv(out, index_label='index', float_format='%.0f')


# Alphabetical grade to quality points
# Guides consulted:
# https://www.gmu.edu/academics/catalog/0203/apolicies/examsgrades.html
# http://catalog.gmu.edu/content.php?catoid=15&navoid=1168
# https://registrar.gmu.edu/topics/special-grades/
grade2pts = {
    'A+':   4.0,
    'A':    4.0,
    'A-':   3.67,
    'B+':   3.33,
    'B':    3.00,
    'B-':   2.67,
    'C+':   2.33,
    'C':    2.00,
    'C-':   1.67,
    'D':    1.00,
    'F':    0.00,
    'IN':   0.00,    # Incomplete
    'S':    3.00,    # Satisfactory (passing; C and up, no effect on GPA)
    'NC':   1.00,    # No Credit (often C- and below)
    'W':    1.00,    # Withdrawal (does not affect grade)
    'NR':   np.nan,  # Not Reported (possibly honor code violation)
    'AU':   np.nan,  # Audit
    'REG':  np.nan,  # ?
    'IX':   np.nan,  # Incomplete Extension
    'IP':   np.nan,  # In Progress
    'nan':  np.nan,  # Unknown
    np.nan: np.nan   # Unknown (for iteration over Series)
}


class PreprocessedCourseData(BasicLuigiTask):
    """Clean up courses data to prepare for learning tasks."""

    def requires(self):
        return {'courses': CoursesData(),
                'admissions': AdmissionsData(),
                'StudentIdMap': StudentIdMap(),
                'CourseIdMap': CourseIdMap(),
                'InstructorIdMap': InstructorIdMap(),
                'OrdinalTermMap': OrdinalTermMap()}

    def run(self):
        with self.input()['courses'].open() as f:
            courses = pd.read_csv(f)

        # fill in missing values for quality points
        # TODO: we can fill in missing lab grades with lecture grades if we can
        # match them up.
        def fill_grdpts(series):
            if series['GRADE'] != np.nan:
                return grade2pts[series['GRADE']]
            else:
                return series['grdpts']

        courses.grdpts = courses.apply(fill_grdpts, axis=1)

        def map_ids(input_name, idname):
            klass = globals()[input_name]
            with self.input()[input_name].open() as f:
                idmap = pd.read_csv(f, index_col=0)

            idmap[idname] = idmap.index
            cols = list(courses.columns.values) + [idname]
            for col_name in klass.ids:
                cols.remove(col_name)

            for col_name in klass.ids:
                dtype = courses[col_name].dtype.type
                idmap[col_name] = idmap[col_name].values.astype(dtype)
            return courses.merge(idmap, how='left', on=klass.ids)[cols]

        # add student cohorts to data frame
        with self.input()['admissions'].open() as f:
            admiss = pd.read_csv(f, usecols=(0,1))

        with self.input()['OrdinalTermMap'].open() as f:
            idmap = pd.read_csv(f, index_col=0)

        admiss.columns = ['id', 'TERMBNR']
        idmap['cohort'] = idmap.index
        admiss = admiss.merge(idmap, how='left', on='TERMBNR')
        del admiss['TERMBNR']
        courses = courses.merge(admiss, how='left', on='id')

        # Replace course, student, instructor and term identifiers with
        # contiguous id mappings
        idmap = {'CourseIdMap': 'cid',
                 'StudentIdMap': 'sid',
                 'InstructorIdMap': 'iid',
                 'OrdinalTermMap': 'termnum'}

        for map_klass, idname in idmap.items():
            courses = map_ids(map_klass, idname)

        # remove unneeded columns
        unneeded = ['CRN', 'SECTNO', 'TITLE',
                    'class', 'instr_rank', 'instr_tenure']
        for col_name in unneeded:
            del courses[col_name]

        # Write cleaned up courses data.
        with self.output().open('w') as out:
            courses.to_csv(out, index=False)


class TrainTestFilter(object):
    """Wrapper class to filter data to train/test sets using cohort/term."""

    term_max = 14  # some number greater than max term id

    def __init__(self, filt):
        if ':' in filt:
            cohort, term = filt.split(':')
            self.cohort_start, self.cohort_end = self._split(cohort)
            self.term_start, self.term_end = self._split(term)
        else:
            self.cohort_start, self.cohort_end = map(int, filt.split('-'))
            self.term_start, self.term_end = (0, self.term_max)

    def _split(self, config):
        if '-' in config:
            return map(int, config.split('-'))
        else:
            return (int(config), self.term_max)

    def __str__(self):
        return '%d_%dT%d_%d' % (
            self.cohort_start, self.cohort_end, self.term_start, self.term_end)

    def mask(self, data):
        return ((data['cohort'] >= self.cohort_start) &
                (data['cohort'] <= self.cohort_end) &
                (data['termnum'] >= self.term_start) &
                (data['termnum'] <= self.term_end))

    def train(self, data):
        return data[self.mask(data)]

    def test(self, data):
        return data[~self.mask(data)]


class UsesTrainTestSplit(luigi.Task):
    """Base task for train/test split args and filters init."""
    train_filters = luigi.Parameter(
        default='0-14',
        description='Specify how to split the train set from the test set.')
    discard_nongrade = luigi.Parameter(
        default=True,
        description='drop W/S/NC grades from training data if True')
    backfill_cold_students = luigi.IntParameter(
        default=3,
        description="number of courses to backfill for cold-start students")
    backfill_cold_courses = luigi.IntParameter(
        default=3,
        description="number of courses to backfill for cold-start courses")

    base = 'data'  # directory to write files to
    ext = 'tsv'    # final file extension for output files
    prefix = 'ucg' # prefix for all output files
    suffix = ''    # class-specific suffix that goes before ext on output names

    @property
    def filters(self):
        return [TrainTestFilter(filt) for filt in self.train_filters.split()]

    def output_base_fname(self):
        parts = [self.prefix] if self.prefix else []

        # parameter suffix part
        param_suffix = '-'.join([str(filt) for filt in self.filters])
        if param_suffix:
            parts.append(param_suffix)

        # indicate if W/S/NC grades are being included in train set
        if not self.discard_nongrade:
            parts.append('ng')

        # indicate whether cold-start backfilling was done for students/courses
        if self.backfill_cold_students:
            parts.append('scs%d' % self.backfill_cold_students)
        if self.backfill_cold_courses:
            parts.append('ccs%d' % self.backfill_cold_courses)

        # include optional class-specific suffix
        if self.suffix:
            parts.append(self.suffix)

        fbase = os.path.join(self.base, '-'.join(parts))
        return '{}.%s.{}'.format(fbase, self.ext)


class DataSplitterBaseTask(UsesTrainTestSplit):
    """Functionality to split train/test data, no run method."""

    def requires(self):
        return PreprocessedCourseData()

    def output(self):
        fname = self.output_base_fname()
        train = fname % 'train'
        test =  fname % 'test'
        return {
            'train': luigi.LocalTarget(train),
            'test': luigi.LocalTarget(test)
        }

    def read_data(self):
        with self.input().open() as f:
            data = pd.read_csv(f)

        # only keep most recent grade
        data = data.drop_duplicates(('sid','cid'), take_last=True)

        # remove records for missing grades
        data = data[~data['grdpts'].isnull()]
        return data


    def backfill(self, train, test, key, firstn):
        """Used to prevent cold-start records.

        :param DataFrame train: The training data.
        :param DataFrame test: The test data.
        :param str key: The key to backfill records for.
        :param int firstn: How many records to backfill for cold-starts.

        """
        if not firstn:  # specified 0 records for backfill
            return (train, test)

        diff = np.setdiff1d(test[key].values, train[key].values)
        diff_mask = test[key].isin(diff)
        diff_records = test[diff_mask]

        # figure out which records to transfer from test set to train set
        # some keys will have less records than specified
        gb = diff_records.groupby(key)
        counts = gb[key].transform('count')
        tokeep = counts - firstn
        tokeep[tokeep < 0] = 0

        # update train/test sets
        removing = gb.head(firstn)
        keeping = gb.tail(tokeep)
        test = pd.concat((test[~diff_mask], keeping))
        train = pd.concat((train, removing))
        return (train, test)

    def split_data(self):
        data = self.read_data()

        # sort data by term number, then by student id
        data = data.sort(['termnum', 'sid'])

        # now do train/test split
        train = pd.concat([f.train(data) for f in self.filters]).drop_duplicates()
        test = pd.concat([f.test(data) for f in self.filters]).drop_duplicates()

        # remove W/S/NC from test set; it never makes sense to test on these
        toremove = ['W', 'S', 'NC']
        test = test[~test.GRADE.isin(toremove)]

        # optionally discard W/S/NC from train set
        if self.discard_nongrade:
            train = train[~train.GRADE.isin(toremove)]

        # if instructed to avoid student/course cold-start,
        # ensure all students/courses in the test set are also in the train set
        train, test = self.backfill(
            train, test, 'sid', self.backfill_cold_students)
        train, test = self.backfill(
            train, test, 'cid', self.backfill_cold_courses)
        return (train, test)


def write_triples(f, data, userid='sid', itemid='cid', rating='grdpts'):
    """Write a data file of triples (sparse matrix).

    :param str userid: Name of user id column (matrix rows).
    :param str itemid: Name of item id column (matrix cols).
    :param str rating: Name of rating column (matrix entries).
    """
    cols = [userid, itemid, rating]
    data.to_csv(f, sep='\t', header=False, index=False, columns=cols)


class UserCourseGradeTriples(DataSplitterBaseTask):
    """Produce a User x Course matrix with quality points as entries."""

    def run(self):
        train, test = self.split_data()

        # write the train/test data
        with self.output()['train'].open('w') as f:
            write_triples(f, train)

        with self.output()['test'].open('w') as f:
            write_triples(f, test)


def write_libfm(f, data, userid='sid', itemid='cid', rating='grdpts',
                timecol='', time_feat_num=0):
    """Write a data file of triples (sparse matrix). This assumes the column ids
    have already been offset by the max row id.

    :param str userid: Name of user id column (matrix rows).
    :param str itemid: Name of item id column (matrix cols).
    :param str rating: Name of rating column (matrix entries).
    :param int time_feat_num: Feature number for time attribute.
    :param str timecol: Name of temporal column.
    """
    # TimeSVD
    if time_feat_num:  # write time as categorical attribute
        def extract_row(series):
            return '%f %d:1 %d:1 %d:%d' % (
                series[rating], series[userid], series[itemid],
                time_feat_num, series[timecol])
    # time-aware BPTF model
    elif timecol:
        def extract_row(series):
            return '%f %d:1 %d:1 %d:1' % (
                series[rating], series[userid], series[itemid], series[timecol])
    # regularized SVD
    else:
        def extract_row(series):
            return '%f %d:1 %d:1' % (
                series[rating], series[userid], series[itemid])

    lines = data.apply(extract_row, axis=1)
    f.write('\n'.join(lines))


class UserCourseGradeLibFM(DataSplitterBaseTask):
    """Output user-course grade matrix in libFM format."""
    time = luigi.Parameter(
        default='',
        description='if empty; no time attributes, ' +
                    'cat = categorical encoding (TimeSVD), ' +
                    'bin = binary, one-hot encoding (BPTF)')
    ext = 'libfm'

    def __init__(self, *args, **kwargs):
        super(UserCourseGradeLibFM, self).__init__(*args, **kwargs)
        if self.time:
            self.suffix = 'time-%s' % self.time

    def run(self):
        train, test = self.split_data()

        # libFM has no notion of columns, it simply takes feature vectors with
        # labels. So we need to re-encode the columns by adding the max row
        # index.
        max_row_idx = max(np.concatenate((test.sid.values, train.sid.values)))
        train.cid += max_row_idx
        test.cid += max_row_idx

        # If time is included, calculate feature number for categorical feature
        if self.time == 'bin':
            max_col_idx = max(
                np.concatenate((test.cid.values, train.cid.values)))
            train.termnum += max_col_idx
            test.termnum += max_col_idx
            def write_libfm_data(f, data):
                write_libfm(f, data, timecol='termnum')
        elif self.time == 'cat':  # categorical, TimeSVD
            max_col_idx = max(
                np.concatenate((test.cid.values, train.cid.values)))
            def write_libfm_data(f, data):
                write_libfm(f, data, timecol='termnum',
                            time_feat_num=max_col_idx + 1)
        else:
            write_libfm_data = write_libfm

        # write the train/test data
        with self.output()['train'].open('w') as f:
            write_libfm_data(f, train)

        with self.output()['test'].open('w') as f:
            write_libfm_data(f, test)


class RunLibFM(UserCourseGradeLibFM):
    iterations = luigi.IntParameter(
        default=100,
        description='number of iterations to use for learning')
    init_stdev = luigi.FloatParameter(
        default=0.3,
        description='initial std of Gaussian spread; higher can be faster')
    use_bias = luigi.BoolParameter(
        default=False,
        description='use global and per-feature bias terms if True')
    dim_start = luigi.IntParameter(
        default=5,
        description='start of dimension range to produce results for')
    dim_end = luigi.IntParameter(
        default=20,
        description='end of dimension range to produce results for, inclusive')

    base = 'outcomes'
    ext = 'tsv'

    def requires(self):
        task_params = [tup[0] for tup in UserCourseGradeLibFM.get_params()]
        params = {k:v for k, v in self.param_kwargs.items()
                  if k in task_params}
        return UserCourseGradeLibFM(**params)

    def output(self):
        parts = []

        # time information part
        if self.time:
            parts.append('time-%s' % self.time)

        # number of iterations part
        parts.append('i%d' % self.iterations)

        # initial standard deviation part (init_stdev)
        std = 's%s' % ''.join(str(self.init_stdev).split('.'))
        parts.append(std)

        # bias terms part
        if self.use_bias:
            parts.append('b')

        self.suffix = '-'.join(parts)
        base_fname = self.output_base_fname()
        fname = base_fname % self.__class__.__name__
        return luigi.LocalTarget(fname)

    def run(self):
        train = self.input()['train'].path
        test = self.input()['test'].path

        results = test_params.test_dim(
            self.dim_start, self.dim_end,
            train, test, self.iterations,
            std=self.init_stdev, bias=self.use_bias)

        with self.output().open('w') as f:
            output = '\n'.join(['\t'.join(result) for result in results])
            f.write(output)


class SVD(RunLibFM):
    """Run libFM to emulate SVD."""
    use_bias = False
    time = ''

class BiasedSVD(SVD):
    """Run libFM to emulate biased SVD."""
    use_bias = True

class TimeSVD(SVD):
    """Run libFM to emulate TimeSVD."""
    time = 'cat'

class BiasedTimeSVD(TimeSVD):
    """Run libFM to emulate biased TimeSVD."""
    use_bias = True

class BPTF(RunLibFM):
    """Run libFM to emulate Bayesian Probabilistic Tensor Factorization."""
    use_bias = False
    time = 'bin'

class BiasedBPTF(BPTF):
    """Run libFM to emulate biased BPTF."""
    use_bias = True


class RunAllOnSplit(RunLibFM):
    """Run all available methods via libFM for a particular train/test split."""
    train_filters = luigi.Parameter(  # restate to make non-optional
        description='Specify how to split the train set from the test set.')
    time = ''     # disable parameter
    use_bias = '' # disable parameter

    def requires(self):
        return [
            SVD(**self.param_kwargs),
            BiasedSVD(**self.param_kwargs),
            TimeSVD(**self.param_kwargs),
            BiasedTimeSVD(**self.param_kwargs),
            BPTF(**self.param_kwargs),
            BiasedBPTF(**self.param_kwargs)
        ]

    def output(self):
        return [luigi.LocalTarget(f.path) for f in self.input()]

    def extract_method_name(self, outfile):
        return os.path.basename(outfile).split('-')[0]

    @property
    def method_names(self):
        return [self.extract_method_name(f.path) for f in self.input()]


class CompareMethods(RunAllOnSplit):
    """Aggregate results from all available methods on a particular split."""
    topn = luigi.IntParameter(
        default=3,
        description="top n results to keep for each method")

    base = 'outcomes'
    ext = 'tsv'
    prefix = 'compare'

    def output(self):
        base_fname = self.output_base_fname()
        fname = base_fname % 'top%d' % self.topn
        return luigi.LocalTarget(fname)

    def requires(self):
        return RunAllOnSplit(train_filters=self.train_filters)

    def read_results(self, f):
        content = f.read()
        rows = [l.split('\t') for l in content.split('\n')]
        rows = [[int(r[0]),float(r[1]),float(r[2])] for r in rows]
        return rows

    def run(self):
        results = []  # store results for all methods
        for input in self.input():
            with input.open() as f:
                rows = self.read_results(f)

            # add method name to each result for this method
            method_name = self.extract_method_name(input.path)
            for row in rows:
                row.insert(0, method_name)

            # keep the top 3 results for each method
            top = list(sorted(rows, key=lambda tup: tup[-1]))
            results += top[:self.topn]

        # now we have results from all methods, sort them
        top = list(sorted(results, key=lambda tup: tup[-1]))
        with self.output().open('w') as f:
            f.write('\t'.join(('method', 'dim', 'train', 'test')) + '\n')
            f.write('\n'.join(['\t'.join(map(str, row)) for row in top]))


class ResultsMarkdownTable(CompareMethods):
    """Produce markdown table of results comparison for a data split."""
    precision = luigi.IntParameter(
        default=5,
        description='number of decimal places to keep for error measurements')

    def requires(self):
        kwargs = self.param_kwargs.copy()
        del kwargs['precision']
        return CompareMethods(**kwargs)

    def output(self):
        outname = self.input().path
        base = os.path.splitext(outname)[0]
        return luigi.LocalTarget('%s.md' % base)

    def read_results(self, f):
        header = f.readline().strip().split('\t')
        content = f.read()
        rows = [l.split('\t') for l in content.split('\n')]
        fmt = '%.{}f'.format(self.precision)
        for row in rows:
            row[2] = fmt % float(row[2])
            row[3] = fmt % float(row[3])
        return header, rows

    def run(self):
        with self.input().open() as f:
            header, rows = self.read_results(f)

        # results are already sorted; we simply need to format them as a
        # markdown table; first find the column widths, leaving a bit of margin
        # space for readability
        widths = np.array([[len(item) for item in row]
                           for row in rows]).max(axis=0)
        margin = 4
        colwidths = widths + margin
        underlines = ['-' * width for width in widths]

        # next, justify the columns appropriately
        def format_row(row):
            return [row[0].ljust(colwidths[0])] + \
                   [row[i].rjust(colwidths[i]) for i in range(1, 4)]

        output = [format_row(header), format_row(underlines)]
        output += [format_row(row) for row in rows]

        # finally, write the table
        with self.output().open('w') as f:
            f.write('\n'.join([''.join(row) for row in output]))


class RunAll(luigi.Task):
    """Run all available methods on 0-4 and 0-7 train/test splits."""

    splits = ["0-4", "0-7"]

    def requires(self):
        for split in self.splits:
            yield ResultsMarkdownTable(train_filters=split)


if __name__ == "__main__":
    luigi.run()
