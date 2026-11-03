#!/usr/bin/env python3
"""OMRON 環境センサー 2JCIE-BU01 の計測値を、1 サンプル 1 行の JSON として標準出力に出し続ける。

--source ble: GATT で接続し、Latest sensing / calculation data の Notify (毎秒) を受信する。
              (アドバタイズは BlueZ が ADV と Scan rsp のメーカーデータを統合してしまい、Sensor data を取りこぼすため使わない)
--source usb: USB シリアルで Latest data long (0x5021) を --sample-interval 秒ごとに読む。
"""
import argparse
import asyncio
import json
import logging
import signal
import struct
import subprocess
import sys
import time

UUID_LATEST_SENSING = "ab705012-0a3a-11e8-ba89-0ed5f89f718b"
UUID_LATEST_CALC = "ab705013-0a3a-11e8-ba89-0ed5f89f718b"
NOTIFY_TIMEOUT_SEC = 30  # 接続中にこの時間 Notify が来なければ再接続する

log = logging.getLogger("2jcie-bu-reader")


def decode_sensor(d: bytes) -> dict:
    temp, humid, light, press, noise, etvoc, eco2 = struct.unpack_from("<hhhihhh", d)
    return {"temperature": temp / 100, "humidity": humid / 100, "light": light, "pressure": press / 1000,
            "noise": noise / 100, "etvoc": etvoc, "eco2": eco2}


def decode_calc(d: bytes) -> dict:
    discomfort, heat, vib, si, pga, seismic = struct.unpack_from("<hhBHHH", d)
    return {"discomfort_index": discomfort / 100, "heat_stroke": heat / 100, "vibration": vib,
            "si": si / 10, "pga": pga / 10, "seismic_intensity": seismic / 1000}


def emit(values: dict):
    print(json.dumps(values), flush=True)


async def run_ble(args):
    """GATT で接続し、Notify を受け続ける。切れたら再接続する。"""
    from bleak import BleakClient, BleakScanner

    state = {"seq": None, "calc": {}, "last_rx": time.monotonic()}

    def on_sensor(_, d: bytearray):
        if d[0] == state["seq"]:  # 同じ計測値の重複受信 (同一シーケンス番号) は 1 回として数える
            return
        state["seq"] = d[0]
        state["last_rx"] = time.monotonic()
        emit({**decode_sensor(bytes(d[1:])), **state["calc"]})

    def on_calc(_, d: bytearray):
        state["calc"] = decode_calc(bytes(d[1:]))  # 次の sensing data と合わせて 1 行にする

    while True:
        try:
            device = await BleakScanner.find_device_by_address(args.address, timeout=20)
            if device is None:
                # 前回の接続が BlueZ に残っているとセンサーがアドバタイズを止めるため、切断してから再試行する
                subprocess.run(["bluetoothctl", "disconnect", args.address], capture_output=True, timeout=10)
                raise IOError(f"{args.address} not found (requested stale connection to be dropped)")
            disconnected = asyncio.Event()
            async with BleakClient(device, disconnected_callback=lambda _: disconnected.set()) as client:
                await client.start_notify(UUID_LATEST_SENSING, on_sensor)
                await client.start_notify(UUID_LATEST_CALC, on_calc)
                log.info("connected: %s", args.address)
                connected_at = time.monotonic()
                while not disconnected.is_set():
                    if time.monotonic() - max(state["last_rx"], connected_at) > NOTIFY_TIMEOUT_SEC:
                        raise IOError(f"no notification for {NOTIFY_TIMEOUT_SEC}s")
                    try:
                        await asyncio.wait_for(disconnected.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        pass
                log.warning("disconnected: %s", args.address)
        except Exception as e:  # 接続失敗・切断は再試行する
            log.warning("ble error: %s: %s", type(e).__name__, e)
        await asyncio.sleep(10)


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
    return {**decode_sensor(d), **decode_calc(d[16:])}


async def run_usb(args):
    import serial

    with serial.Serial(args.port, 115200, timeout=2) as ser:
        while True:
            try:
                emit(read_usb_latest(ser))
            except Exception as e:  # 1 回の失敗で止めず、次のサンプルで再試行する
                log.warning("usb read failed: %s", e)
            await asyncio.sleep(args.sample_interval)


async def run(args):
    task = asyncio.create_task(run_ble(args) if args.source == "ble" else run_usb(args))
    # SIGTERM でも BLE 接続を閉じてから終わる (閉じないと BlueZ に接続が残る)
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        pass


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", choices=["ble", "usb"], default="ble")
    p.add_argument("--address", help="BLE: 接続するセンサーの MAC アドレス")
    p.add_argument("--port", default="/dev/2jcie-bu", help="USB: シリアルポート")
    p.add_argument("--sample-interval", type=float, default=5, help="USB: 読み取り間隔 (秒)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.source == "ble" and not args.address:
        p.error("--source ble requires --address")

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
