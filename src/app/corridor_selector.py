"""Implements UI for selecting corridors."""
from tkinter import ttk
import tkinter as tk
from enum import Enum
from typing import Dict, List

from srctools import Property
from srctools.dmx import Element, Attribute as DMAttr, ValueType as DMXValue
import srctools.logger
import attrs
import trio

import packages
from app import TK_ROOT, config, dragdrop, img, sound, tk_tools
from app.richTextBox import tkRichText
from localisation import gettext
from packages import corridor
import consts
import utils


LOGGER = srctools.logger.get_logger(__name__)
WIDTH = 96 + (32 if utils.MAC else 16)
HEIGHT = 64 + 51


class RandMode(Enum):
    """Kind of randomisation to use."""
    SINGLE = 'single'
    EDITOR = 'editor'  # 4 or 7, depending on editor instance count.
    ALL = 'all'  # Use all regardless.


@config.register('Corridor', uses_id=True)
@attrs.frozen
class CorridorConf(config.Data):
    """The current configuration for a corridor."""
    selected: List[str] = attrs.Factory(list)
    random: RandMode = RandMode.EDITOR

    @staticmethod
    def get_id(
        style: str,
        mode: corridor.GameMode,
        direction: corridor.Direction,
        orient: corridor.CorrOrient,
    ) -> str:
        """Given the style and kind of corridor, return the ID for config lookup."""
        return f'{style.casefold()}:{mode.value}_{direction.value}_{orient.value}'

    @classmethod
    def parse_kv1(cls, data: Property, version: int) -> 'CorridorConf':
        """Parse from KeyValues1 configs."""
        assert version == 1, version
        corr = [
            prop.value
            for prop in data.find_children('selected')
        ]
        try:
            rand = RandMode(data['random', 'editor'])
        except ValueError:
            rand = RandMode.EDITOR

        return CorridorConf(corr, rand)

    def export_kv1(self) -> Property:
        """Serialise to a Keyvalues1 config."""
        return Property('Corridor', [
            Property('random', self.random.value),
            Property('Selected', [
                Property('Corridor', corr)
                for corr in self.selected
            ])
        ])

    @classmethod
    def parse_dmx(cls, data: Element, version: int) -> 'CorridorConf':
        """Parse from DMX configs."""
        assert version == 1, version
        try:
            rand = RandMode(data['random'].val_str)
        except (KeyError, TypeError, ValueError):
            rand = RandMode.EDITOR
        try:
            corr = list(data['selected'].iter_str())
        except KeyError:
            corr = []

        return CorridorConf(corr, rand)

    def export_dmx(self) -> Element:
        """Serialise to DMX configs."""
        elem = Element('Corridor', 'DMEConfig')
        elem['random'] = self.random.value
        elem['selected'] = selected = DMAttr.array('selected', DMXValue.STR)
        selected.extend(self.selected)
        return elem


# If no groups are defined for a style, use this.
FALLBACK = corridor.CorridorGroup(
    '<Fallback>',
    {
        (mode, direction, orient): []
        for mode in corridor.GameMode
        for direction in corridor.Direction
        for orient in corridor.CorrOrient
    }
)


class Selector:
    """Corridor selection UI."""
    win: tk.Toplevel
    drag_man: dragdrop.Manager[corridor.Corridor]

    # Current corridor on the right side.
    wid_image: ttk.Label
    wid_title: ttk.Label
    wid_desc: tkRichText

    # The 7 selected slots, and the rest.
    selected: List[dragdrop.Slot[corridor.Corridor]]
    unused: List[dragdrop.Slot[corridor.Corridor]]

    # The current corridor group for the selected style, and the config ID to save/load.
    # These are updated by load_corridors().
    corr_group: corridor.CorridorGroup
    conf_id: str

    def __init__(self, packset: packages.PackagesSet) -> None:
        self.win = tk.Toplevel(TK_ROOT)
        self.win.wm_protocol("WM_DELETE_WINDOW", self.hide)

        self.win.rowconfigure(0, weight=1)
        self.win.columnconfigure(0, weight=1)

        frm_left = ttk.Frame(self.win, relief="sunken")
        frm_left.rowconfigure(0, weight=1)
        frm_left.columnconfigure(0, weight=1)
        frm_left.grid(row=0, column=0, sticky='nsew')

        frm_right = ttk.Frame(self.win)
        frm_right.columnconfigure(0, weight=1)
        frm_right.grid(row=0, column=1, sticky='ns')

        self.wid_image = ttk.Label(frm_right)
        self.wid_image.grid(row=0, column=0, sticky='ew')
        img.apply(self.wid_image, img.Handle.builtin('BEE2/corr_generic', 256, 192))

        self.wid_title = ttk.Label(frm_right, text='Corridor')
        self.wid_title.grid(row=1, column=0, sticky='ew')

        self.wid_desc = tkRichText(frm_right)
        self.wid_desc.grid(row=2, column=0, sticky='nsew')
        frm_right.rowconfigure(2, weight=1)

        ttk.Button(frm_right, text=gettext('Close'), command=self.hide).grid(row=3, column=0)

        refresh = self.refresh

        button_frm = ttk.Frame(frm_left)
        button_frm.grid(row=0, column=0, columnspan=3)
        self.btn_mode = tk_tools.EnumButton(
            button_frm,
            (corridor.GameMode.SP, gettext('SP')),
            (corridor.GameMode.COOP, gettext('Coop')),
            callback=refresh,
        )
        self.btn_direction = tk_tools.EnumButton(
            button_frm,
            (corridor.Direction.ENTRY, gettext('Entry')),
            (corridor.Direction.EXIT, gettext('Exit')),
            callback=refresh,
        )
        self.btn_orient = tk_tools.EnumButton(
            button_frm,
            (corridor.CorrOrient.FLAT, gettext('Flat')),
            (corridor.CorrOrient.UP, gettext('Upward')),
            (corridor.CorrOrient.DN, gettext('Downward')),
            callback=refresh,
        )
        self.btn_mode.frame.grid(row=0, column=0, padx=8)
        self.btn_direction.frame.grid(row=0, column=1, padx=8)
        self.btn_orient.frame.grid(row=0, column=2, padx=8)

        self.canvas = tk.Canvas(frm_left)
        self.canvas.grid(row=1, column=0, columnspan=3, sticky='nsew')
        frm_left.rowconfigure(1, weight=1)

        self.drag_man = drop = dragdrop.Manager(self.win, size=(WIDTH, HEIGHT))
        self.selected = [
            drop.slot_target(self.canvas)
            for _ in range(7)
        ]
        self.unused_count = 0
        self.load_corridors(packset)

    def show(self) -> None:
        """Display the window."""
        self.drag_man.load_icons()
        self.win.deiconify()

    def hide(self) -> None:
        """Hide the window."""
        self.win.withdraw()
        self.drag_man.unload_icons()

    def load_corridors(self, packset: packages.PackagesSet) -> None:
        """Fetch the current set of corridors from this style."""
        style_id = config.get_cur_conf(
            config.LastSelected, 'Style',
            config.LastSelected('BEE2_CLEAN_STYLE'),
        ).id or 'BEE2_CLEAN_STYLE'
        try:
            self.corr_group = packset.obj_by_id(corridor.CorridorGroup, style_id)
        except KeyError:
            LOGGER.warning('No corridors defined for style "{}"', style_id)
            self.corr_group = FALLBACK
        self.conf_id = CorridorConf.get_id(
            style_id,
            self.btn_mode.current,
            self.btn_direction.current,
            self.btn_orient.current,
        )

    async def refresh(self, _=None) -> None:
        """Called to update the slots with new items if the corridor set changes."""
        LOGGER.info(
            'Mode: {}, Dir: {}, Orient: {}',
            self.btn_mode.current, self.btn_direction.current, self.btn_orient.current,
        )
        self.conf_id = CorridorConf.get_id(
            self.corr_group.id,
            self.btn_mode.current,
            self.btn_direction.current,
            self.btn_orient.current,
        )
        conf = config.get_cur_conf(CorridorConf, self.conf_id, CorridorConf())

        corr_list = self.corr_group.corridors[
            self.btn_mode.current,
            self.btn_direction.current,
            self.btn_orient.current,
        ]

        # Ensure enough flexible slots exist to hold all of them, and clear em all.
        for slot in self.drag_man.flexi_slots():
            slot.contents = None
        for _ in range(len(corr_list) - self.unused_count):
            self.drag_man.slot_flexi(self.canvas)
        self.unused_count = len(corr_list)

        inst_to_corr = {corr.instance.casefold(): corr for corr in corr_list}
        if conf.selected:
            for slot, sel_id in zip(self.selected, conf.selected):
                try:
                    slot.contents = inst_to_corr.pop(sel_id.casefold())
                except KeyError:
                    LOGGER.warning('Unknown corridor instance "{}" in config!')
            for slot, corr in zip(
                self.drag_man.flexi_slots(),
                sorted(inst_to_corr, key=lambda corr: corr.name),
            ):
                slot.contents = corr
        else:
            # No configuration, populate with the defaults.
            for corr in corr_list:
                if corr.orig_index > 0:
                    self.selected[corr.orig_index - 1].contents = corr
        await self.reflow()

    async def reflow(self, _=None) -> None:
        """Called to reposition the corridors."""
        self.drag_man.flow_slots(self.canvas, [*self.drag_man.targets(), *self.drag_man.flexi_slots()])


async def test() -> None:
    from app import background_run
    background_run(img.init, {})
    background_run(sound.sound_task)

    test_sel = Selector(packages.LOADED)
    config.read_settings()
    test_sel.show()
    with trio.CancelScope() as scope:
        test_sel.win.wm_protocol('WM_DELETE_WINDOW', scope.cancel)
        await trio.sleep_forever()