#!/usr/bin/env python3
"""2JCIE-BU01 の Advertise setting (0x5115) を USB 経由で読み書きする。"""
import argparse
import struct
import time

import serial

ADDR_ADV_SETTING = 0x5115


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def request(ser, command, address, data=b""):
    body = struct.pack("<BH", command, address) + data
    frame = b"RB" + struct.pack("<H", len(body) + 2) + body
    ser.reset_input_buffer()
    ser.write(frame + struct.pack("<H", crc16(frame)))
    head = ser.read(4)
    resp = head + ser.read(struct.unpack("<H", head[2:4])[0])
    if crc16(resp[:-2]) != struct.unpack("<H", resp[-2:])[0]:
        raise IOError("CRC mismatch")
    if resp[4] != command:
        raise IOError(f"error response: {resp.hex()}")
    return resp[7:-2]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", default="/dev/2jcie-bu")
    p.add_argument("--interval-ms", type=int, help="発信間隔 (100-10240 ms)")
    p.add_argument("--mode", type=lambda v: int(v, 0), help="発信モード (例: 0x03)")
    args = p.parse_args()

    with serial.Serial(args.port, 115200, timeout=2) as ser:
        interval, mode = struct.unpack("<HB", request(ser, 0x01, ADDR_ADV_SETTING)[:3])
        print(f"current: interval={interval * 0.625:.0f}ms mode=0x{mode:02x}")
        if args.interval_ms is None and args.mode is None:
            return
        new_interval = round((args.interval_ms or interval * 0.625) / 0.625)
        new_mode = mode if args.mode is None else args.mode
        # USB ではフラッシュ状態 (0x5403) を読めないため、書き込み後に読み直して確認する
        request(ser, 0x02, ADDR_ADV_SETTING, struct.pack("<HB", new_interval, new_mode))
        time.sleep(1)
        interval, mode = struct.unpack("<HB", request(ser, 0x01, ADDR_ADV_SETTING)[:3])
        print(f"updated: interval={interval * 0.625:.0f}ms mode=0x{mode:02x}")


if __name__ == "__main__":
    main()
