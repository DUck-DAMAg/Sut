# -*- coding: utf-8 -*-
"""Мобильное приложение «Суть»: ищет сразу в нескольких поисковиках,
читает найденные сайты и показывает только главное — прямо в приложении."""
import os
import threading
import webbrowser
from urllib.parse import urlparse

import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())  # HTTPS на Android

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton
from kivy.utils import escape_markup, platform

from search_core import DEFAULT_ENGINES, run_search

BG = (0.07, 0.08, 0.10, 1)
CARD = (0.12, 0.14, 0.18, 1)
ACCENT = "#8ab4f8"
MUTED = "#8b94a3"


def open_url(url):
    if platform == "android":
        try:
            from jnius import autoclass

            Intent = autoclass("android.content.Intent")
            Uri = autoclass("android.net.Uri")
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
            return
        except Exception:
            pass
    webbrowser.open(url)


class Wrap(Label):
    """Текст с переносом строк, сам подбирает высоту."""

    def __init__(self, **kw):
        kw.setdefault("markup", True)
        kw.setdefault("size_hint_y", None)
        kw.setdefault("halign", "left")
        kw.setdefault("valign", "top")
        kw.setdefault("font_size", sp(15))
        super().__init__(**kw)
        self.bind(width=lambda *_: setattr(self, "text_size", (self.width, None)))
        self.bind(texture_size=lambda *_: setattr(self, "height", self.texture_size[1] + dp(4)))


class Card(BoxLayout):
    """Блок с фоном, внутри — вертикальный список."""

    def __init__(self, **kw):
        super().__init__(orientation="vertical", size_hint_y=None,
                         padding=dp(10), spacing=dp(4), **kw)
        from kivy.graphics import Color, RoundedRectangle

        with self.canvas.before:
            Color(*CARD)
            self._bg = RoundedRectangle(radius=[dp(10)])
        self.bind(pos=self._upd, size=self._upd, minimum_height=self.setter("height"))

    def _upd(self, *_):
        self._bg.pos, self._bg.size = self.pos, self.size


class SutApp(App):
    title = "Суть"

    def build(self):
        Window.clearcolor = BG
        Window.softinput_mode = "below_target"
        self.busy = False
        self.urls = []

        root = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))

        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        self.input = TextInput(
            hint_text="Что найти?", multiline=False, font_size=sp(16),
            background_color=(0.16, 0.18, 0.23, 1), foreground_color=(1, 1, 1, 1),
            hint_text_color=(0.55, 0.58, 0.64, 1), cursor_color=(1, 1, 1, 1),
            padding=[dp(10), dp(12)],
        )
        self.input.bind(on_text_validate=self.start_search)
        self.btn = Button(text="Найти", size_hint_x=0.28, font_size=sp(16),
                          background_normal="", background_color=(0.26, 0.52, 0.96, 1))
        self.btn.bind(on_release=self.start_search)
        row.add_widget(self.input)
        row.add_widget(self.btn)
        root.add_widget(row)

        eng_row = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(4))
        self.toggles = {}
        for name in DEFAULT_ENGINES:
            tb = ToggleButton(text=name, state="down", font_size=sp(11),
                              background_normal="", background_down="",
                              background_color=(0.2, 0.24, 0.3, 1))
            tb.bind(state=self._tint)
            self._tint(tb, "down")
            self.toggles[name] = tb
            eng_row.add_widget(tb)
        root.add_widget(eng_row)

        self.status = Wrap(text="", color=(0.7, 0.74, 0.8, 1), font_size=sp(13))
        root.add_widget(self.status)

        scroll = ScrollView(do_scroll_x=False)
        self.results = GridLayout(cols=1, size_hint_y=None, spacing=dp(8), padding=[0, 0, 0, dp(16)])
        self.results.bind(minimum_height=self.results.setter("height"))
        scroll.add_widget(self.results)
        root.add_widget(scroll)
        return root

    @staticmethod
    def _tint(tb, state):
        tb.background_color = (0.26, 0.52, 0.96, 1) if state == "down" else (0.2, 0.24, 0.3, 1)

    # --- поиск -----------------------------------------------------------
    def start_search(self, *_):
        q = self.input.text.strip()
        if not q or self.busy:
            return
        engines = [n for n, b in self.toggles.items() if b.state == "down"]
        if not engines:
            self.status.text = "Выберите хотя бы один поисковик"
            return
        self.busy = True
        self.btn.disabled = True
        self.results.clear_widgets()
        self.status.text = "Начинаю поиск…"
        threading.Thread(target=self._worker, args=(q, engines), daemon=True).start()

    def _worker(self, q, engines):
        def progress(msg):
            Clock.schedule_once(lambda dt, m=msg: setattr(self.status, "text", m))

        try:
            res = run_search(q, engines, progress=progress)
            Clock.schedule_once(lambda dt: self.show(res))
        except Exception as e:
            Clock.schedule_once(lambda dt, e=e: self.fail(e))

    def fail(self, e):
        self.busy = False
        self.btn.disabled = False
        self.status.text = "Ошибка: %s" % e

    def show(self, res):
        self.busy = False
        self.btn.disabled = False
        self.urls = [s["url"] for s in res["sources"]]
        self.results.clear_widgets()

        if not res["sources"]:
            msg = "Ничего не найдено."
            if res["errors"]:
                msg += " Проблемы: " + "; ".join("%s — %s" % kv for kv in res["errors"].items())
            self.status.text = msg
            return

        note = "Источников: %d" % len(res["sources"])
        if res["errors"]:
            note += " · недоступны: " + ", ".join(res["errors"])
        self.status.text = note

        # Общая суть
        card = Card()
        card.add_widget(Wrap(text="[b][color=%s]Суть[/color][/b]" % ACCENT, font_size=sp(18)))
        for s in res["summary"]:
            n = s["source"] + 1
            card.add_widget(Wrap(
                text="• %s [ref=%d][color=%s](%d)[/color][/ref]" % (escape_markup(s["text"]), s["source"], ACCENT, n),
                on_ref_press=self._ref,
            ))
        self.results.add_widget(card)

        # По источникам
        for i, s in enumerate(res["sources"]):
            c = Card()
            host = urlparse(s["url"]).netloc.replace("www.", "")
            c.add_widget(Wrap(
                text="[b][ref=%d][color=%s]%d. %s[/color][/ref][/b]" % (i, ACCENT, i + 1, escape_markup(s["title"])),
                on_ref_press=self._ref,
            ))
            c.add_widget(Wrap(
                text="[color=%s]%s · %s%s[/color]" % (
                    MUTED, escape_markup(host), ", ".join(s["engines"]),
                    "" if s["full_text"] else " · только описание"),
                font_size=sp(12),
            ))
            for e in s["essence"]:
                c.add_widget(Wrap(text="– " + escape_markup(e)))
            self.results.add_widget(c)

    def _ref(self, _label, ref):
        try:
            open_url(self.urls[int(ref)])
        except Exception:
            pass


if __name__ == "__main__":
    SutApp().run()
