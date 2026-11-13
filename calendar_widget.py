# -*- coding: utf-8 -*-
"""
ווידג'ט יומן לשולחן העבודה (Windows) – בסגנון macOS.
קורא יומני Google דרך "הכתובת הסודית בפורמט iCal" ומציג אותם בצבעים.
לקריאה בלבד: כל העריכות נעשות באייפון / ב-Google, והווידג'ט מתעדכן לבד.

קליק ימני על הווידג'ט: רענון / עריכת הגדרות / יציאה.
גרירה עם העכבר: הזזת הווידג'ט (המיקום נשמר).
"""
import json
import os
import sys
import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import icalendar
import recurring_ical_events
from PySide6.QtCore import Qt, QTimer, QPoint, Signal, QObject
from PySide6.QtGui import QColor, QPainter, QPainterPath, QAction, QFont, QCursor
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QMenu,
    QScrollArea, QSizePolicy,
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
STATE_PATH = os.path.join(APP_DIR, "state.json")
CACHE_PATH = os.path.join(APP_DIR, "cache.json")

HEB_DAYS = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]  # date.weekday(): Mon=0
HEB_MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי",
              "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]

THEMES = {
    "dark": {
        "bg": QColor(28, 28, 30, 215), "border": QColor(255, 255, 255, 28),
        "text": "#F5F5F7", "sub": "#98989D", "accent": "#FF453A",
        "row_bg": "rgba(255,255,255,0.06)", "divider": "rgba(255,255,255,0.10)",
    },
    "light": {
        "bg": QColor(246, 246, 248, 225), "border": QColor(0, 0, 0, 25),
        "text": "#1D1D1F", "sub": "#6E6E73", "accent": "#FF3B30",
        "row_bg": "rgba(0,0,0,0.04)", "divider": "rgba(0,0,0,0.08)",
    },
}


# ---------------------------------------------------------------- data ----
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def to_local(value, tz):
    """מחזיר (datetime מקומי, האם אירוע של יום שלם)."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=tz)
        return value.astimezone(tz), False
    return datetime(value.year, value.month, value.day, tzinfo=tz), True


def fetch_events(config):
    """מוריד את כל היומנים ומחזיר רשימת אירועים לימים הקרובים."""
    tz = ZoneInfo(config.get("timezone", "Asia/Jerusalem"))
    days_ahead = int(config.get("days_ahead", 7))
    now = datetime.now(tz)
    start = datetime(now.year, now.month, now.day, tzinfo=tz)
    end = start + timedelta(days=days_ahead)
    events, errors, parsed = [], [], []

    for cal in config.get("calendars", []):
        url = (cal.get("url") or "").strip()
        if not url or "PASTE" in url:
            continue
        try:
            if url.startswith("http"):
                r = requests.get(url, timeout=20)
                r.raise_for_status()
                raw = r.content
            else:  # קובץ ics מקומי (לבדיקות)
                with open(url, "rb") as f:
                    raw = f.read()
            ical = icalendar.Calendar.from_ical(raw)
            parsed.append((cal, ical))
            for ev in recurring_ical_events.of(ical).between(start, end):
                if str(ev.get("STATUS", "")).upper() == "CANCELLED":
                    continue
                s, all_day = to_local(ev.get("DTSTART").dt, tz)
                e_raw = ev.get("DTEND")
                e = to_local(e_raw.dt, tz)[0] if e_raw else s + timedelta(hours=1)
                events.append({
                    "title": str(ev.get("SUMMARY", "(ללא כותרת)")),
                    "location": str(ev.get("LOCATION", "") or ""),
                    "start": s.isoformat(), "end": e.isoformat(),
                    "all_day": all_day, "calendar": cal.get("name", ""),
                    "color": cal.get("color", "#0A84FF"),
                    "is_exam": bool(cal.get("exams", False)),
                })
        except Exception as ex:  # noqa: BLE001
            errors.append(f"{cal.get('name', '?')}: {ex.__class__.__name__}")

    # ספירה לאחור למבחן הקרוב – מחפשים עד 180 יום קדימה ביומני מבחנים
    next_exam = None
    for cal, ical in parsed:
        if not cal.get("exams"):
            continue
        try:
            for ev in recurring_ical_events.of(ical).between(start, start + timedelta(days=180)):
                s, _ = to_local(ev.get("DTSTART").dt, tz)
                if s.date() >= now.date() and (next_exam is None or s.isoformat() < next_exam["start"]):
                    next_exam = {"title": str(ev.get("SUMMARY", "")), "start": s.isoformat(),
                                 "color": cal.get("color", "#FF453A")}
        except Exception:
            pass

    events.sort(key=lambda x: (x["start"], not x["all_day"]))
    return {"events": events, "errors": errors, "next_exam": next_exam,
            "updated": now.isoformat()}


class Fetcher(QObject):
    done = Signal(dict)

    def run(self, config):
        threading.Thread(target=lambda: self.done.emit(fetch_events(config)), daemon=True).start()


# ------------------------------------------------------------------ UI ----
def rgba(hex_color, alpha):
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


class Widget(QWidget):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.theme = THEMES.get(config.get("theme", "dark"), THEMES["dark"])
        self.data = load_json(CACHE_PATH, {"events": [], "errors": [], "next_exam": None})
        self._drag = None

        flags = Qt.FramelessWindowHint | Qt.Tool
        flags |= Qt.WindowStaysOnTopHint if config.get("always_on_top") else Qt.WindowStaysOnBottomHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setFixedWidth(int(config.get("width", 340)))

        font = QFont(config.get("font", "Segoe UI"))
        font.setPointSizeF(9.5)
        self.setFont(font)

        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(18, 16, 18, 14)
        self.root.setSpacing(8)

        state = load_json(STATE_PATH, {})
        if "x" in state:
            self.move(state["x"], state["y"])
        else:
            geo = QApplication.primaryScreen().availableGeometry()
            self.move(geo.right() - self.width() - 30, geo.top() + 30)

        self.fetcher = Fetcher()
        self.fetcher.done.connect(self.on_data)

        self.render()
        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(int(config.get("refresh_minutes", 5)) * 60 * 1000)
        # מעבר יום / עדכון "עכשיו" – ציור מחדש כל דקה
        self.tick = QTimer(self)
        self.tick.timeout.connect(self.render)
        self.tick.start(60 * 1000)

    # ---------------- data
    def refresh(self):
        self.fetcher.run(self.config)

    def on_data(self, data):
        if data["events"] or not data["errors"]:
            self.data = data
            save_json(CACHE_PATH, data)
        else:  # אין אינטרנט – נשארים עם המטמון ומציגים שגיאה
            self.data["errors"] = data["errors"]
        self.render()

    # ---------------- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(1, 1, -1, -1), 20, 20)
        p.fillPath(path, self.theme["bg"])
        p.setPen(self.theme["border"])
        p.drawPath(path)

    def clear(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self.clear(item.layout())

    def label(self, text, size=9.5, color=None, bold=False, wrap=False):
        lbl = QLabel("\u200f" + text)  # RLM: כיווניות מימין לשמאל גם כשהטקסט מתחיל במספר
        f = QFont(self.font())
        f.setPointSizeF(size)
        f.setWeight(QFont.DemiBold if bold else QFont.Normal)
        lbl.setFont(f)
        lbl.setStyleSheet(f"color:{color or self.theme['text']}; background:transparent;")
        lbl.setWordWrap(wrap)
        lbl.setAlignment(Qt.AlignAbsolute | Qt.AlignRight | Qt.AlignVCenter)
        return lbl

    def render(self):
        t = self.theme
        tz = ZoneInfo(self.config.get("timezone", "Asia/Jerusalem"))
        now = datetime.now(tz)
        self.clear(self.root)

        # כותרת: יום ותאריך
        head = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(self.label(f"יום {HEB_DAYS[now.weekday()]}", 10, t["accent"], True))
        left.addWidget(self.label(f"{now.day} ב{HEB_MONTHS[now.month - 1]}", 20, t["text"], True))
        head.addLayout(left)
        head.addStretch()
        exam = self.data.get("next_exam")
        if exam:
            d = (datetime.fromisoformat(exam["start"]).date() - now.date()).days
            box = QVBoxLayout()
            box.setSpacing(0)
            n = self.label("היום!" if d == 0 else str(d), 20, exam["color"], True)
            n.setAlignment(Qt.AlignCenter)
            sub = self.label("למבחן" if d == 0 else ("יום למבחן" if d == 1 else "ימים למבחן"), 8, t["sub"])
            sub.setAlignment(Qt.AlignCenter)
            box.addWidget(n)
            box.addWidget(sub)
            w = QWidget()
            w.setLayout(box)
            w.setToolTip(exam["title"])
            w.setStyleSheet(f"background:{t['row_bg']}; border-radius:12px;")
            head.addWidget(w)
        self.root.addLayout(head)

        # אירועים לפי יום
        by_day = {}
        for ev in self.data.get("events", []):
            s = datetime.fromisoformat(ev["start"]).astimezone(tz)
            e = datetime.fromisoformat(ev["end"]).astimezone(tz)
            if not ev["all_day"] and e < now:
                continue  # אירועים שהסתיימו היום – מוסתרים
            by_day.setdefault(s.date(), []).append((s, e, ev))

        max_rows = int(self.config.get("max_events", 14))
        shown = 0
        if not by_day:
            self.root.addWidget(self.label("אין אירועים בימים הקרובים ✨", 10, t["sub"]))
        for day in sorted(by_day):
            if shown >= max_rows:
                break
            delta = (day - now.date()).days
            title = "היום" if delta == 0 else "מחר" if delta == 1 else \
                f"יום {HEB_DAYS[day.weekday()]}, \u2066{day.day}.{day.month}\u2069"
            div = QFrame()
            div.setFixedHeight(1)
            div.setStyleSheet(f"background:{t['divider']};")
            self.root.addWidget(div)
            self.root.addWidget(self.label(title, 9, t["sub"], True))
            for s, e, ev in by_day[day]:
                if shown >= max_rows:
                    break
                self.root.addWidget(self.event_row(s, e, ev, now))
                shown += 1

        # שורת סטטוס
        foot = self.data.get("errors") or []
        upd = self.data.get("updated")
        txt = "⚠ " + " | ".join(foot) if foot else \
            (f"עודכן {datetime.fromisoformat(upd).astimezone(tz):%H:%M}" if upd else "טוען…")
        self.root.addWidget(self.label(txt, 7.5, t["sub"]))
        self.adjustSize()
        self.update()

    def event_row(self, s, e, ev, now):
        t = self.theme
        row = QFrame()
        live = (not ev["all_day"]) and s <= now <= e
        row.setStyleSheet(
            f"QFrame{{background:{t['row_bg'] if not live else rgba(ev['color'], 0.22)}; border-radius:10px;}}")
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(8)
        bar = QFrame()
        bar.setFixedWidth(4)
        bar.setStyleSheet(f"background:{ev['color']}; border-radius:2px;")
        h.addWidget(bar)
        v = QVBoxLayout()
        v.setSpacing(1)
        title = self.label(ev["title"], 9.5, t["text"], True, wrap=True)
        title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        v.addWidget(title)
        if ev["all_day"]:
            info = "כל היום"
        else:
            info = f"\u2066{s:%H:%M}–{e:%H:%M}\u2069"  # בידוד LTR לשעות
            if live:
                info = "עכשיו · " + info
        if ev.get("location"):
            info += f"  ·  {ev['location']}"
        v.addWidget(self.label(info, 8, t["sub"], wrap=True))
        h.addLayout(v, 1)
        row.setToolTip(ev["calendar"])
        return row

    # ---------------- interaction
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        if self._drag is not None:
            self._drag = None
            save_json(STATE_PATH, {"x": self.x(), "y": self.y()})

    def contextMenuEvent(self, _):
        m = QMenu(self)
        m.setLayoutDirection(Qt.RightToLeft)
        a1, a2, a3 = QAction("רענן עכשיו", m), QAction("פתח הגדרות", m), QAction("יציאה", m)
        a1.triggered.connect(self.refresh)
        a2.triggered.connect(lambda: os.startfile(CONFIG_PATH) if hasattr(os, "startfile") else None)
        a3.triggered.connect(QApplication.quit)
        for a in (a1, a2):
            m.addAction(a)
        m.addSeparator()
        m.addAction(a3)
        m.exec(QCursor.pos())


def main():
    example = os.path.join(APP_DIR, "config.example.json")
    if not os.path.exists(CONFIG_PATH) and os.path.exists(example):
        import shutil
        shutil.copyfile(example, CONFIG_PATH)  # first run: create a private config from the template
    config = load_json(CONFIG_PATH, None)
    if config is None:
        print("לא נמצא config.json ליד הקובץ.")
        sys.exit(1)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    w = Widget(config)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
