# 2jcie-bu-reader

OMRON 環境センサー 2JCIE-BU01 (USB 型) の計測値を、1 サンプル 1 行の JSON として標準出力に出し続けるコマンドです。

[harvest-relay](https://github.com/okeee/harvest-relay) の stream センサーとして使うと、1 分ごとに集約して SORACOM Harvest Data に送り、SORACOM Lagoon で推移を見られます。単独でも `2jcie-bu-reader --address ... | jq` のように使えます。

```json
{"temperature": 24.5, "humidity": 46.12, "light": 120, "pressure": 1015.204, "noise": 56.3, "etvoc": 1475, "eco2": 2357, "discomfort_index": 70.79, "heat_stroke": 20.31, "vibration": 0, "si": 0.0, "pga": 0.0, "seismic_intensity": 0.0}
```

| フィールド | 単位 | | フィールド | 単位 |
|---|---|---|---|---|
| temperature | ℃ | | eco2 | ppm (eTVOC からの推定値) |
| humidity | %RH | | discomfort_index | 不快指数 |
| light | lx | | heat_stroke | 熱中症警戒度 (WBGT 相当, ℃) |
| pressure | hPa | | vibration | 振動情報 (0: なし, 1: 振動中 (地震判定中), 2: 地震中) |
| noise | dB | | si / pga | SI 値 (kine) / PGA (gal) |
| etvoc | ppb | | seismic_intensity | 震度相当値 |

> eCO2 は NDIR 方式の CO2 センサーではなく、eTVOC からの推定値です。芳香剤や調理のにおいなどで大きく上がるため、換気の目安として使うときはにおいの元から離して設置してください。

## 接続方法

| `--source` | 方式 | 特徴 |
|---|---|---|
| `ble` (既定) | GATT 接続し、Notify を毎秒受信 | センサーを USB 電源につなぐだけで、離れた場所に置ける |
| `usb` | USB シリアルで `--sample-interval` 秒ごとに読み取り | Bluetooth が使えない端末向け |

BLE ではアドバタイズではなく GATT の Notify を使います。BlueZ がアドバタイズの ADV と Scan response のメーカーデータを 1 つに統合してしまい、計測値を取りこぼすためです。

## 必要なもの

- Raspberry Pi などの Linux 端末 (Debian 13 / Raspberry Pi OS で確認)
- Python 3.11 以上と、以下のパッケージ

```bash
sudo apt install python3-bleak python3-serial bluez
```

## セットアップ

```bash
sudo install -m 755 2jcie-bu-reader.py /usr/local/bin/2jcie-bu-reader
```

### BLE で使う場合

センサーの MAC アドレスを調べます (名前は `Rbt-Sensor`)。

```bash
bluetoothctl --timeout 15 scan on | grep Rbt
```

```bash
2jcie-bu-reader --address XX:XX:XX:XX:XX:XX
```

Bluetooth がブロックされている場合は `sudo rfkill unblock bluetooth` で解除します。

### USB で使う場合

2JCIE-BU01 は Linux 標準の ftdi_sio ドライバーに自動では認識されないため、udev ルールでドライバーを割り当て、`/dev/2jcie-bu` として見えるようにします。

```bash
sudo cp udev/99-2jcie-bu.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
# センサーを挿し直す
2jcie-bu-reader --source usb
```

### harvest-relay で Harvest Data に送る

[harvest-relay.example.toml](harvest-relay.example.toml) の `[[source]]` を harvest-relay の設定ファイルに加えます。

## Lagoon ダッシュボード

[lagoon/dashboard.json](lagoon/dashboard.json) を SORACOM Lagoon 3 の Dashboards → Import から読み込みます。インポート時にデータソースと IMSI (Harvest Data に送っている SIM / Arc の IMSI) を指定します。

温度・湿度・eCO2・熱中症警戒度などの現在値と、各値の推移、BLE の受信件数を表示します。パネル構成を変える場合は `tools/gen_lagoon_dashboard.py` を編集して再生成します。

## センサーの設定 (アドバタイズ間隔)

BLE の Notify は計測周期ごとに届きます。アドバタイズの設定は USB 接続中に `tools/adv-setting.py` で読み書きできます。

```bash
tools/adv-setting.py                                  # 現在の設定を表示
tools/adv-setting.py --interval-ms 1000 --mode 0x03   # 1 秒間隔に変更
```

## 参考

- [2JCIE-BU01 ユーザーズマニュアル (A279)](https://omronfs.omron.com/ja_JP/ecb/products/pdf/CDSC-015.pdf)

## ライセンス

MIT
