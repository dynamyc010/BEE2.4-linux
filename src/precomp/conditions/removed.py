"""Conditions that were present in older versions only."""
from precomp.conditions import RES_EXHAUSTED, make_flag, make_result
import srctools.logger

COND_MOD_NAME = 'Removed'

LOGGER = srctools.logger.get_logger(__name__)


def deprecator(func, ret_val):
    """Deprecate a flag or result."""
    def do_dep(name: str, *aliases: str, msg: str = None):
        used = False
        if msg:
            msg = f'{name} is no longer used.\n{msg}'
        else:
            msg = f'{name} is no longer used.'

        def deprecated():
            """This result is no longer used."""
            nonlocal used
            if not used:
                used = True
                LOGGER.warning(msg)

            return ret_val

        func(name, *aliases)(deprecated)
    return do_dep


deprecate_result = deprecator(make_result, RES_EXHAUSTED)
deprecate_flag = deprecator(make_flag, False)


deprecate_result('HollowBrush')
deprecate_result(
    'MarkLocking',
    msg='Configure locking items in the enhanced editoritems configuration.',
)

deprecate_flag(
    'LockingIO',
    msg='Configure locking items in the enhanced editoritems configuration.',
)

deprecate_result(
    'FaithBullseye',
    msg='Faith Plate targets are now entirely generated by the compiler.',
)