#!/usr/bin/env python3
"""OMRON 2JCIE-BU01 の値を集め、1 周期ごとに集約して SORACOM Unified Endpoint (Harvest Data) に送信する。

--source ble: GATT で接続し、Latest sensing / calculation data の Notify (毎秒) を受信する。
              (アドバタイズは BlueZ が ADV と Scan rsp のメーカーデータを統合してしまい、Sensor data を取りこぼすため使わない)
--source usb: USB シリアルで Latest data long (0x5021) を --sample-interval 秒ごとに読む。
"""
import argparse
import asyncio
import json
import logging
import statistics
import struct
import sys
import time

import requests

ENDPOINT = "http://uni.soracom.io"
UUID_LATEST_SENSING = "ab705012-0a3a-11e8-ba89-0ed5f89f718b"
UUID_LATEST_CALC = "ab705013-0a3a-11e8-ba89-0ed5f89f718b"
NOTIFY_TIMEOUT_SEC = 30  # 接続中にこの時間 Notify が来なければ再接続する
STALE_EXIT_SEC = 300     # この時間データを受け取れなければ終了し、systemd に再起動させる

# 集約方法: 環境値は平均、瞬間的に変わる値は最大も、地震・振動系は最大のみ
MEAN_FIELDS = ["temperature", "humidity", "pressure", "etvoc", "eco2", "discomfort_index", "heat_stroke"]
MEAN_MAX_FIELDS = ["noise", "light"]
MAX_FIELDS = ["vibration", "si", "pga", "seismic_intensity"]

log = logging.getLogger("2jcie-harvest")


def decode_sensor(d: bytes) -> dict:
    temp, humid, light, press, noise, etvoc, eco2 = struct.unpack_from("<hhhihhh", d)
    return {"temperature": temp / 100, "humidity": humid / 100, "light": light, "pressure": press / 1000,
            "noise": noise / 100, "etvoc": etvoc, "eco2": eco2}


def decode_calc(d: bytes) -> dict:
    discomfort, heat, vib, si, pga, seismic = struct.unpack_from("<hhBHHH", d)
    return {"discomfort_index": discomfort / 100, "heat_stroke": heat / 100, "vibration": vib,
            "si": si / 10, "pga": pga / 10, "seismic_intensity": seismic / 1000}


class Aggregator:
    def __init__(self):
        self.samples = {}
        self.last_seq = {}
        self.last_rx = time.monotonic()

    def add(self, kind: str, seq, values: dict):
        if seq is not None:  # 同じ計測値の重複受信 (同一シーケンス番号) は 1 回として数える
            if self.last_seq.get(kind) == seq:
                return
            self.last_seq[kind] = seq
        self.last_rx = time.monotonic()
        for k, v in values.items():
            self.samples.setdefault(k, []).append(v)

    def flush(self) -> dict:
        s, self.samples = self.samples, {}
        out = {}
        for k in MEAN_FIELDS + MEAN_MAX_FIELDS:
            if s.get(k):
                out[k] = round(statistics.fmean(s[k]), 3)
        for k in MEAN_MAX_FIELDS:
            if s.get(k):
                out[f"{k}_max"] = max(s[k])
        for k in MAX_FIELDS:
            if s.get(k):
                out[k] = max(s[k])
        if out:
            out["samples"] = len(s.get("temperature", []))
        return out


def send(payload: dict, dry_run: bool):
    if dry_run:
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        return
    try:
        r = requests.post(ENDPOINT, json=payload, timeout=10)
        if r.ok:
            log.info("sent: %s %s", r.status_code, json.dumps(payload))
        else:
            log.warning("send failed: %s %s", r.status_code, r.text[:200])
    except requests.RequestException as e:
        log.warning("send failed: %s", e)


async def run_ble(args, agg: Aggregator):
    reporter = asyncio.create_task(report_loop(args, agg))
    connector = asyncio.create_task(ble_connect_loop(args, agg))
    done, pending = await asyncio.wait({reporter, connector}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    for t in done:
        t.result()  # report_loop の RuntimeError をそのまま伝える


async def ble_connect_loop(args, agg: Aggregator):
    """GATT で接続し、Latest sensing / calculation data の Notify (毎秒) を受け続ける。切れたら再接続する。"""
    from bleak import BleakClient, BleakScanner

    def on_sensor(_, d: bytearray):
        agg.add("sensor", d[0], decode_sensor(bytes(d[1:])))

    def on_calc(_, d: bytearray):
        agg.add("calc", d[0], decode_calc(bytes(d[1:])))

    while True:
        try:
            device = await BleakScanner.find_device_by_address(args.address, timeout=20)
            if device is None:
                raise IOError(f"{args.address} not found")
            disconnected = asyncio.Event()
            async with BleakClient(device, disconnected_callback=lambda _: disconnected.set()) as client:
                await client.start_notify(UUID_LATEST_SENSING, on_sensor)
                await client.start_notify(UUID_LATEST_CALC, on_calc)
                log.info("connected: %s", args.address)
                while not disconnected.is_set():
                    if time.monotonic() - agg.last_rx > NOTIFY_TIMEOUT_SEC:
                        raise IOError(f"no notification for {NOTIFY_TIMEOUT_SEC}s")
                    try:
                        await asyncio.wait_for(disconnected.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        pass
                log.warning("disconnected: %s", args.address)
        except Exception as e:  # 接続失敗・切断は再試行する (長時間続けば report_loop が終了させる)
            log.warning("ble error: %s", e)
        await asyncio.sleep(10)


async def run_usb(args, agg: Aggregator):
    import serial

    with serial.Serial(args.port, 115200, timeout=2) as ser:
        async def sampler():
            while True:
                try:
                    values = read_usb_latest(ser)
                    agg.add("usb", None, values)
                except Exception as e:  # 1 回の失敗で止めず、次のサンプルで再試行する
                    log.warning("usb read failed: %s", e)
                await asyncio.sleep(args.sample_interval)

        task = asyncio.create_task(sampler())
        try:
            await report_loop(args, agg)
        finally:
            task.cancel()


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def read_usb_latest(ser) -> dict:
    body = struct.pack("<BH", 0x01, 0x5021)  # Latest data long
    frame = b"RB" + struct.pack("<H", len(body) + 2) + body
    ser.reset_input_buffer()
    ser.write(frame + struct.pack("<H", crc16(frame)))
    head = ser.read(4)
    if len(head) != 4 or head[:2] != b"RB":
        raise IOError(f"invalid header: {head.hex()}")
    resp = head + ser.read(struct.unpack("<H", head[2:4])[0])
    if crc16(resp[:-2]) != struct.unpack("<H", resp[-2:])[0]:
        raise IOError("CRC mismatch")
    if resp[4] != 0x01:
        raise IOError(f"error response: code=0x{resp[4]:02x}")
    d = resp[8:]  # header(2) + length(2) + command(1) + address(2) + sequence(1)
    values = decode_sensor(d)
    values.update(decode_calc(d[16:]))
    return values


async def report_loop(args, agg: Aggregator):
    while True:
        await asyncio.sleep(args.once_after if args.once else args.interval)
        payload = agg.flush()
        if payload:
            send(payload, args.dry_run)
        else:
            log.warning("no samples in the last %ss", args.interval)
        if args.once:
            return
        if args.source == "ble" and time.monotonic() - agg.last_rx > STALE_EXIT_SEC:
            raise RuntimeError(f"no BLE advertisement for {STALE_EXIT_SEC}s")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--source", choices=["ble", "usb"], default="ble")
    p.add_argument("--address", help="BLE: 接続するセンサの MAC アドレス")
    p.add_argument("--port", default="/dev/2jcie-bu", help="USB: シリアルポート")
    p.add_argument("--interval", type=int, default=60, help="送信間隔 (秒)")
    p.add_argument("--sample-interval", type=int, default=5, help="USB: 読み取り間隔 (秒)")
    p.add_argument("--once", action="store_true", help="--once-after 秒集めて 1 回だけ送信して終了")
    p.add_argument("--once-after", type=int, default=15)
    p.add_argument("--dry-run", action="store_true", help="送信せず標準出力に表示")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.source == "ble" and not args.address:
        p.error("--source ble requires --address")

    agg = Aggregator()
    try:
        asyncio.run(run_ble(args, agg) if args.source == "ble" else run_usb(args, agg))
    except RuntimeError as e:
        log.error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
