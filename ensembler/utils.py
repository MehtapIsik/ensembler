import contextlib
import os
import logging
import functools
import shutil
import tempfile
from msmseeder.core import mpistate

logger = logging.getLogger('info')


def nonefn():
    return None


def mpirank0only(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if mpistate.rank == 0:
            fn(*args, **kwargs)
    return wrapper


def mpirank0only_and_end_with_barrier(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if mpistate.rank == 0:
            fn(*args, **kwargs)
        mpistate.comm.Barrier()
    return wrapper


# def notify_when_done(fn):
#     @functools.wraps(fn)
#     def print_done(*args, **kwargs):
#         try:
#             import mpi4py.MPI
#             comm = mpi4py.MPI.COMM_WORLD
#             rank = comm.rank
#             if rank == 0:
#                 fn(*args, **kwargs)
#                 logger.info('Done.')
#         except ImportError:
#             fn(*args, **kwargs)
#             logger.info('Done.')
#
#     return print_done


def notify_when_done(fn):
    @functools.wraps(fn)
    def print_done(*args, **kwargs):
        fn(*args, **kwargs)
        log_done()
    return print_done


@mpirank0only
def log_done():
    logger.info('Done.')


def create_dir(dirpath):
    """
    :param dirpath: str
    """
    try:
        os.makedirs(dirpath)
        logger.info('Created directory "%s"' % dirpath)
    except OSError as e:
        if e.errno == 17:
            logger.debug('Directory "%s" already exists - will not overwrite' % dirpath)
        else:
            raise


def file_exists_and_not_empty(filepath):
    if os.path.exists(filepath):
        if os.path.getsize(filepath) > 0:
            return True
    return False


def loglevel_setter(logger, loglevel):
    if loglevel is not None:
        loglevel_obj = getattr(logging, loglevel.upper())
        logger.setLevel(loglevel_obj)


@contextlib.contextmanager
def enter_temp_dir():
    """Create a temporary directory, enter, yield, exit, rmdir; used as context manager."""
    temp_dir = tempfile.mkdtemp()
    cwd = os.getcwd()
    os.chdir(temp_dir)
    yield temp_dir
    os.chdir(cwd)
    shutil.rmtree(temp_dir)