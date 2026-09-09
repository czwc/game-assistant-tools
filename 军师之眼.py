# -*- coding: utf-8 -*-
"""
军师之眼 —— 吉祥当铺半自动砍价助手（电脑端）

用法：配合小滴云手机电脑版使用。
1. 点【框选识别区域】，在小滴云手机的游戏画面上拖出一个包含
   "顾客开价 / 耐心值" 的矩形区域（一次即可，会记住）。
2. 点【识别一次】或勾选【自动识别(3秒)】。
3. 工具自动 OCR 画面里的数字并填入开价/估价/耐心，给出推荐出价。
4. 你自己在游戏里手动点击出价。

本工具只提供建议，绝不代点、不注入、不模拟按键。
"""
import re
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np
from PIL import Image

try:
    import mss
except ImportError:
    mss = None

_ocr = None
_ocr_lock = threading.Lock()


def get_ocr():
    global _ocr
    with _ocr_lock:
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr = RapidOCR()
    return _ocr


def capture_region(region):
    """截取指定区域，小图放大 2 倍以提高 OCR 命中率"""
    with mss.mss() as sct:
        shot = sct.grab(region)
    img = Image.frombytes("RGB", shot.size, shot.rgb)
    if img.width < 1200:
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    return img


def run_ocr(img):
    arr = np.array(img)
    result, _ = get_ocr()(arr)
    lines = []
    if result:
        for box, text, conf in result:
            lines.append({
                "text": text.strip(),
                "conf": float(conf),
                "y": min(p[1] for p in box),
                "x": min(p[0] for p in box),
            })
    lines.sort(key=lambda l: (l["y"], l["x"]))
    return lines


def extract_numbers(text):
    t = text.replace(",", "").replace("，", "").replace(".", "")
    return [int(n) for n in re.findall(r"\d+", t)]


def parse_patience(lines):
    """优先找含'耐心'的行；否则找 0~100 的独立小数字"""
    for l in lines:
        if "耐心" in l["text"]:
            for n in extract_numbers(l["text"]):
                if 0 <= n <= 100:
                    return n
    candidates = []
    for l in lines:
        nums = extract_numbers(l["text"])
        if len(nums) == 1 and 0 <= nums[0] <= 100:
            candidates.append(nums[0])
    return candidates[0] if candidates else None


def parse_prices(lines):
    """返回候选价格列表（去掉 0~100 的小数字和明显非价格数值），按从大到小"""
    nums = []
    for l in lines:
        for n in extract_numbers(l["text"]):
            if n > 100:
                nums.append(n)
    return sorted(set(nums), reverse=True)


def advise(ask, est, patience):
    """分段压价模型：耐心越少，出价越接近成交价。返回 (出价, 阶段说明)"""
    if not ask or not est:
        return None, ""
    if patience > 70:
        ratio, stage = 0.30, "开局试探"
    elif patience > 50:
        ratio, stage = 0.45, "中盘压价"
    elif patience > 35:
        ratio, stage = 0.62, "甜点区边缘"
    elif patience > 25:
        ratio, stage = 0.80, "甜点区收网"
    else:
        ratio, stage = 0.92, "危险区保底"
    bid = round(est * ratio)
    if patience > 25 and bid > ask * 0.95:
        bid = round(ask * 0.95)
    bid = max(bid, 1)
    return bid, stage


class RegionSelector(tk.Toplevel):
    """全屏半透明遮罩，拖拽框选识别区域"""

    def __init__(self, master, callback):
        super().__init__(master)
        self.callback = callback
        self.configure(bg="black")
        self.attributes("-alpha", 0.3)
        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        self.canvas = tk.Canvas(self, cursor="cross", bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.create_text(
            self.winfo_screenwidth() // 2, 40,
            text="在小滴云手机游戏画面上按住鼠标拖出一个矩形（含开价与耐心值），松开完成",
            fill="yellow", font=("Microsoft YaHei", 14),
        )
        self.start = None
        self.rect = None
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

    def on_press(self, e):
        self.start = (e.x_root, e.y_root)
        if self.rect:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(
            e.x, e.y, e.x, e.y, outline="red", width=2)

    def on_drag(self, e):
        if self.start:
            self.canvas.coords(self.rect, self.start[0], self.start[1], e.x, e.y)

    def on_release(self, e):
        x1, y1 = self.start
        x2, y2 = e.x_root, e.y_root
        left, top = min(x1, x2), min(y1, y2)
        w, h = abs(x2 - x1), abs(y2 - y1)
        self.destroy()
        if w > 10 and h > 10:
            self.callback({"left": left, "top": top, "width": w, "height": h})


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("军师之眼 · 吉祥当铺砍价助手（只建议，不代点）")
        self.attributes("-topmost", True)
        self.configure(bg="#1a1208")
        self.region = None
        self.auto = tk.BooleanVar(value=False)
        self._auto_thread = None

        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("TButton", padding=6)
        style.configure("TScale", background="#1a1208")

        pad = {"padx": 10, "pady": 6}

        top = tk.Frame(self, bg="#1a1208")
        top.pack(fill="x", **pad)
        tk.Button(top, text="① 框选识别区域", command=self.select_region,
                  bg="#e8b64c", fg="#1a1208", relief="flat",
                  font=("Microsoft YaHei", 11, "bold")).pack(side="left", padx=(0, 8))
        tk.Button(top, text="② 识别一次", command=self.recognize_once,
                  bg="#7ee08a", fg="#1a1208", relief="flat",
                  font=("Microsoft YaHei", 11, "bold")).pack(side="left", padx=(0, 8))
        tk.Checkbutton(top, text="自动识别(3秒)", variable=self.auto,
                       command=self.toggle_auto, bg="#1a1208", fg="#f0e6d2",
                       selectcolor="#2d1f0e", activebackground="#1a1208",
                       font=("Microsoft YaHei", 10)).pack(side="left")

        self.status = tk.Label(self, text="请先框选识别区域", bg="#1a1208",
                               fg="#a8926b", font=("Microsoft YaHei", 9), anchor="w")
        self.status.pack(fill="x", padx=10)

        self.ocr_box = tk.Text(self, height=5, bg="#241a0c", fg="#c9b48c",
                               relief="flat", font=("Microsoft YaHei", 9))
        self.ocr_box.pack(fill="x", padx=10, pady=(0, 4))
        self.ocr_box.insert("1.0", "（OCR 识别出的文字会显示在这里，游戏字体花哨时请以人工校对为准）")
        self.ocr_box.configure(state="disabled")

        grid = tk.Frame(self, bg="#1a1208")
        grid.pack(fill="x", **pad)

        tk.Label(grid, text="顾客开价", bg="#1a1208", fg="#c9b48c",
                 font=("Microsoft YaHei", 10)).grid(row=0, column=0, sticky="e")
        self.ask_var = tk.StringVar()
        self.ask_cb = ttk.Combobox(grid, textvariable=self.ask_var, width=12)
        self.ask_cb.grid(row=0, column=1, padx=(4, 14))

        tk.Label(grid, text="你的估价", bg="#1a1208", fg="#c9b48c",
                 font=("Microsoft YaHei", 10)).grid(row=0, column=2, sticky="e")
        self.est_var = tk.StringVar()
        self.est_cb = ttk.Combobox(grid, textvariable=self.est_var, width=12)
        self.est_cb.grid(row=0, column=3, padx=4)
        self.est_cb.bind("<<ComboboxSelected>>", lambda e: self.update_advice())
        self.ask_cb.bind("<<ComboboxSelected>>", lambda e: self.update_advice())
        self.ask_var.trace_add("write", lambda *a: self.update_advice())
        self.est_var.trace_add("write", lambda *a: self.update_advice())

        prow = tk.Frame(self, bg="#1a1208")
        prow.pack(fill="x", **pad)
        tk.Label(prow, text="耐心值", bg="#1a1208", fg="#c9b48c",
                 font=("Microsoft YaHei", 10)).pack(side="left")
        self.pat_label = tk.Label(prow, text="100", bg="#1a1208", fg="#e8b64c",
                                  font=("Microsoft YaHei", 14, "bold"), width=4)
        self.pat_label.pack(side="left")
        self.pat_scale = tk.Scale(prow, from_=100, to=0, orient="horizontal",
                                  command=self.on_patience, bg="#1a1208",
                                  fg="#f0e6d2", highlightthickness=0,
                                  troughcolor="#2d1f0e", length=320)
        self.pat_scale.set(100)
        self.pat_scale.pack(side="left", fill="x", expand=True, padx=8)

        self.bid_label = tk.Label(self, text="--", bg="#1a1208", fg="#7ee08a",
                                  font=("Microsoft YaHei", 34, "bold"))
        self.bid_label.pack(pady=(4, 0))
        self.stage_label = tk.Label(self, text="框选区域并识别后，推荐出价显示在这里",
                                    bg="#1a1208", fg="#c9b48c",
                                    font=("Microsoft YaHei", 10))
        self.stage_label.pack(pady=(0, 8))

        tk.Label(self, text="出价请在游戏里手动点击 · 本工具绝不自动操作游戏",
                 bg="#1a1208", fg="#6b5c42", font=("Microsoft YaHei", 9)).pack(pady=(0, 8))

    # ---------- 功能 ----------
    def select_region(self):
        RegionSelector(self, self._set_region)

    def _set_region(self, region):
        self.region = region
        self.set_status(f"识别区域已设定：{region['width']}x{region['height']} @({region['left']},{region['top']})")

    def set_status(self, text):
        self.status.configure(text=text)

    def recognize_once(self):
        if not self.region:
            messagebox.showinfo("提示", "请先点【框选识别区域】框出游戏画面")
            return
        if mss is None:
            messagebox.showerror("缺少依赖", "请先安装 mss：pip install mss")
            return
        self.set_status("识别中…")
        threading.Thread(target=self._recognize_worker, daemon=True).start()

    def _recognize_worker(self):
        try:
            img = capture_region(self.region)
            lines = run_ocr(img)
            self.after(0, lambda: self._apply_ocr(lines))
        except Exception as e:
            self.after(0, lambda: self.set_status(f"识别失败：{e}"))

    def _apply_ocr(self, lines):
        self.ocr_box.configure(state="normal")
        self.ocr_box.delete("1.0", "end")
        for l in lines:
            self.ocr_box.insert("end", f"{l['text']}   (置信度 {l['conf']:.2f})\n")
        self.ocr_box.configure(state="disabled")

        pat = parse_patience(lines)
        if pat is not None:
            self.pat_scale.set(pat)

        prices = parse_prices(lines)
        vals = [str(p) for p in prices[:8]]
        self.ask_cb["values"] = vals
        self.est_cb["values"] = vals
        if prices:
            # 默认：最大数为开价；次大或其 7 折为估价
            ask = prices[0]
            est = prices[1] if len(prices) > 1 else round(ask * 0.7)
            self.ask_var.set(str(ask))
            self.est_var.set(str(est))
            self.set_status(f"识别到 {len(lines)} 行文字，耐心={pat if pat is not None else '?'}，请核对数字")
        else:
            self.set_status("没识别到价格数字，试试调大框选区域或手动输入")

    def toggle_auto(self):
        if self.auto.get():
            if not self.region:
                messagebox.showinfo("提示", "请先框选识别区域")
                self.auto.set(False)
                return
            self._auto_thread = threading.Thread(target=self._auto_loop, daemon=True)
            self._auto_thread.start()
            self.set_status("自动识别已开启（每 3 秒）")
        else:
            self.set_status("自动识别已关闭")

    def _auto_loop(self):
        while self.auto.get():
            self.recognize_once()
            time.sleep(3)

    def on_patience(self, value):
        self.pat_label.configure(text=str(int(float(value))))
        self.update_advice()

    def update_advice(self):
        try:
            ask = int(float(self.ask_var.get() or 0))
            est = int(float(self.est_var.get() or 0))
        except ValueError:
            ask = est = 0
        p = int(float(self.pat_scale.get()))
        bid, stage = advise(ask, est, p)
        if bid is None:
            self.bid_label.configure(text="--")
            self.stage_label.configure(text="填好开价和估价后显示推荐出价")
            return
        color = "#7ee08a" if p > 35 else ("#e8b64c" if p > 25 else "#f0784c")
        self.bid_label.configure(text=f"出价 {bid:,}", fg=color)
        tip = {"开局试探": "大胆压，他不会走",
               "中盘压价": "往估价一半靠",
               "甜点区边缘": "黄金压价期",
               "甜点区收网": "耐心剩30左右成交最划算",
               "危险区保底": "再不成交他就要走了"}.get(stage, "")
        ratio_pct = {0.30: "30%", 0.45: "45%", 0.62: "62%",
                     0.80: "80%", 0.92: "92%"}[0.30 if p > 70 else 0.45 if p > 50
                                                 else 0.62 if p > 35 else 0.80 if p > 25 else 0.92]
        self.stage_label.configure(text=f"【{stage}】{tip} · 建议出价为估价的 {ratio_pct}", fg=color)


if __name__ == "__main__":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 保证截图坐标与屏幕一致
    except Exception:
        pass
    app = App()
    app.mainloop()
