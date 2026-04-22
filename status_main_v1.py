import network
import urequests
import ujson as json
import utime as time
from machine import Pin
import ssl
import usocket  # for miner TCP connections
import gc
import random
from config import cfg
from machine import WDT
import machine
wdt = WDT(timeout=300000)  # 5 minutes
gc.collect()
def debug_messages(message_data):
    if(DEBUG == True):
        print(str(message_data))


print("Now Runing Main File....")

DEBUG = True
WIFI_SSID = cfg["WIFI_SSID"]
WIFI_PASS = cfg["WIFI_PASS"]



#FIREBASE_relay_url = "https://gopal-8d955.firebaseio.com/minerhub1/relay.json"
FIREBASE_relay_url = "https://gopal-8d955.firebaseio.com/" + cfg["HUB"] + "/relay.json"

#FIREBASE_status_url = "https://gopal-8d955.firebaseio.com/minerhub1/status.json"
FIREBASE_status_url = "https://gopal-8d955.firebaseio.com/" + cfg["HUB"] + "/status.json"


REBOOT_URL = ""
if cfg["ESP32_Board"] == "relay_board":
    REBOOT_URL = "https://gopal-8d955.firebaseio.com/" + cfg["HUB"] + "/reboot_relay_esp32_board.json"
elif cfg["ESP32_Board"] == "status_board":
    REBOOT_URL = "https://gopal-8d955.firebaseio.com/" + cfg["HUB"] + "/reboot_status_esp32_board.json"
else:
    print("ESP32_Board TYPE Not Found")


wlan = None

def connect_wifi():
    global wlan

    while True:

        if wlan is None:
            wlan = network.WLAN(network.STA_IF)
            wlan.active(True)
            wlan.config(pm=network.WLAN.PM_NONE)  # disable power save (important)

        if wlan.isconnected():
            return True

        try:
            wlan.disconnect()
            time.sleep(1)
            wlan.connect(WIFI_SSID, WIFI_PASS)
        except Exception as e:
            debug_messages("WiFi error: " + str(e))

        # wait max 10 seconds
        for _ in range(10):
            if wlan.isconnected():
                debug_messages("✅ Connected (DHCP IP): " + str(wlan.ifconfig()))
                return True
            time.sleep(1)

        debug_messages("⚠️ WiFi connection problem... retry in 60s")
        time.sleep(60)

def check_internet():

    for attempt in range(5):
        """
        Simple internet check by opening TCP connection to a public DNS server.
        """
        global wlan
        if wlan is None or not wlan.isconnected():
            connect_wifi()

        s = None
        try:
            s = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
            s.settimeout(10)
            # You can change to 1.1.1.1 if 8.8.8.8 is blocked
            s.connect(("8.8.8.8", 53))
            s.close()
            return True
        except Exception as e:
            debug_messages("check_internet error:" +  str(e))
            try:
                if s:
                    s.close()
            except:
                pass
            debug_messages("Internet Connection Problem...")
            time.sleep(60)



MINER_IPS = [
    "192.168.1.77",
    "192.168.1.78",
    "192.168.1.79"
]
PORT = 4028
TIMEOUT = 3.0  # seconds



# ========= TEMP SAFETY =========
TEMP_LIMIT = 80               # °C
TEMP_CHECK_INTERVAL = 30     # 5 minutes (300 seconds)
_last_temp_check = 0



miner_data = []
def relay_status_update(ip_string,relay_status):
    debug_messages("relay_status_update Working...")
    relay_json = {str(ip_string):str(relay_status)}
    try:
        r = urequests.patch(FIREBASE_relay_url, json=relay_json)
        r.close()
    except:
        debug_messages("relay_status_update except")
        pass
def check_temps_and_protect():
    global _last_temp_check, miner_data
    debug_messages("check_temps_and_protect Working...")
    miner_data = []

    def format_hms(seconds):
        if seconds is None:
            return "N/A"
        try:
            seconds = int(seconds)
            h = seconds // 3600
            m = (seconds % 3600) // 60
            s = seconds % 60
            return "%02d:%02d:%02d" % (h, m, s)
        except:
            return "N/A"

    def parse_ip_port(s):
        if ":" in s:
            ip, port = s.split(":", 1)
            return ip.strip(), int(port.strip())
        return s.strip(), PORT

    def recv_all(sock):
        sock.settimeout(TIMEOUT)
        data = b""
        try:
            while True:
                chunk = sock.recv(512)
                if not chunk:
                    break
                data += chunk
        except:
            pass
        return data

    def split_into_json_objects(text):
        if "}{" in text:
            text = text.replace("}{", "}\n{")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        objs = []
        for ln in lines:
            try:
                objs.append(json.loads(ln))
            except:
                pass
        return objs

    def get_stats_from_miner(ip, port):
        sock = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
        sock.settimeout(TIMEOUT)

        try:
            cmd = json.dumps({"command": "stats"}) + "\n"
            sock.connect((ip, port))
            sock.send(cmd.encode("ascii"))
            data = recv_all(sock)
        except:
            data = b""
        finally:
            try:
                sock.close()
            except:
                pass

        if not data:
            return []

        try:
            text = data.decode("utf-8", "ignore").strip()
            parsed = [json.loads(text)]
            return parsed
        except:
            return split_into_json_objects(text)

    def extract_fields(parsed):
        """
        Extract temps, uptime (seconds), and hashrate from miner JSON response.
        Works with both 'stats' and 'summary' outputs.
        """

        temp1 = temp2 = temp3 = None
        uptime = None
        hashrate = None

        for obj in parsed:
            if not isinstance(obj, dict):
                continue

            # ---- STATS / SUMMARY blocks ----
            for block_key in ("STATS", "SUMMARY"):
                if block_key in obj and isinstance(obj[block_key], list):
                    for entry in obj[block_key]:
                        if not isinstance(entry, dict):
                            continue

                        for k, v in entry.items():
                            kl = k.lower()

                            # ---- Temperatures ----
                            if kl == "temp_chip1":
                                temp1 = v
                            elif kl == "temp_chip2":
                                temp2 = v
                            elif kl == "temp_chip3":
                                temp3 = v

                            # ---- Uptime (seconds) ----
                            elif kl in ("elapsed", "uptime", "elapsedtime"):
                                uptime = v

                            # ---- Hashrate ----
                            elif kl in ("ghs 5s", "ghs av", "mhs 5s", "mhs av"):
                                if hashrate is None:
                                    hashrate = v

            # ---- Flat dict fallback ----
            for k, v in obj.items():
                kl = k.lower()
                if kl in ("elapsed", "uptime", "elapsedtime") and uptime is None:
                    uptime = v

        # ---- Convert uptime safely ----
        try:
            uptime_seconds = int(float(uptime)) if uptime is not None else None
        except:
            uptime_seconds = None

        return {
            "temp_chip1": temp1,
            "temp_chip2": temp2,
            "temp_chip3": temp3,
            "uptime_seconds": uptime_seconds,
            "hashrate": hashrate,
        }


    def highest_last_two(t1, t2, t3):
        vals = []
        for t in (t1, t2, t3):
            try:
                vals.append(int(str(t)[-2:]))
            except:
                pass
        return max(vals) if vals else 0


    def hashrate_return(hashrate):
        try:
            convert_hash = int(int(float(str(hashrate))) / 1000)
            return str(convert_hash)
        except:
            return "0"

    def uptime_return(uptime):
        try:
            return uptime
        except:
            return 0






    for miner in MINER_IPS:
        ip, port = parse_ip_port(miner)
        try:
            parsed = get_stats_from_miner(ip, port)

            if not parsed:
                miner_data_each = {"ip": str(ip[-2:]), "temp": "0", "hash": "0", "uptime": "0"}
                miner_data.append(miner_data_each)
                #print(miner_data)
                continue

            f = extract_fields(parsed)
            temps = [f["temp_chip1"], f["temp_chip2"], f["temp_chip3"]]

            t1 = f["temp_chip1"]
            t2 = f["temp_chip2"]
            t3 = f["temp_chip3"]
            up = format_hms(f["uptime_seconds"])
            hr = f["hashrate"]

            line = (
                    "%s -> T1=%s, T2=%s, T3=%s | Hashrate=%s | Uptime=%s"
                    % (ip, t1, t2, t3, hr, up)
            )



            height_temp = highest_last_two(t1, t2, t3)
            #miner_data_item = "ip:" + str(ip[-2:] + "  temp:" + str(height_temp) + "  hash:" + str(hashrate_return(hr)) + "  uptime:" + str(uptime_return(up)))
            ip_string = str(ip[-2:])
            miner_data_each = {"ip":str(ip[-2:]),"temp":height_temp,"hash":str(hashrate_return(hr)),"uptime":str(uptime_return(up))}
            miner_data.append(miner_data_each)
            #miner_data[str(ip[-2:])] = {"temp":str(height_temp),"hash":str(hashrate_return(hr)),"uptime":str(uptime_return(up))}
            #miner_data[str(miner)] = f



            for t in temps:
                if not t:
                    continue

                # If temp is like "50-50-60-60"
                if isinstance(t, str) and "-" in t:
                    parts = t.split("-")
                else:
                    parts = [t]

                for p in parts:
                    try:
                        temp_val = float(p)
                        print("Parsed temp:", temp_val)

                        if temp_val > TEMP_LIMIT:
                            current_status = "off"
                            relay_status_update(ip_string, current_status)
                            debug_messages("ALERT: Temperature exceeded " + str(ip) + " " + str(temp_val))
                            return
                    except:
                        pass
        except:
            miner_data_each = {"ip": str(ip[-2:]), "temp": "0", "hash": "0", "uptime": "0"}
            miner_data.append(miner_data_each)
def miner_status_update():
    debug_messages("miner_status_update Working...")
    #print(miner_data)
    try:
        miner_data_1 = [str(miner_data[0]["ip"]), str(miner_data[0]["hash"]), str(miner_data[0]["uptime"]),
                        str(miner_data[0]["temp"])]
        miner_data_2 = [str(miner_data[1]["ip"]), str(miner_data[1]["hash"]), str(miner_data[1]["uptime"]),
                        str(miner_data[1]["temp"])]
        miner_data_3 = [str(miner_data[2]["ip"]), str(miner_data[2]["hash"]), str(miner_data[2]["uptime"]),
                        str(miner_data[2]["temp"])]
        miners_data_send = {
            "miner1": miner_data_1,
            "miner2": miner_data_2,
            "miner3": miner_data_3
        }
        r = urequests.put(FIREBASE_status_url, json=miners_data_send)
        r.close()
    except:
        #print(miner_data)
        debug_messages("miner_status_update except")
        pass
def check_reboot_esp32_board():
    try:
        debug_messages("Checking reboot flag...")

        r = urequests.get(REBOOT_URL)
        data = r.text.strip().replace('"', '')
        r.close()

        debug_messages("Reboot flag: " + str(data))

        if data.lower() == "yes":
            debug_messages("🔄 Reboot command received!")

            # 🔥 reset flag to avoid loop
            try:
                urequests.put(REBOOT_URL, data='"no"')
            except:
                pass

            time.sleep(2)
            machine.reset()

    except Exception as e:
        debug_messages("Reboot check error: " + str(e))
def main():
    global internet_ok, _internet_forced_off
    global BOOT_TIME

    time.sleep(30)

    # 1. Connect WiFi and sync time
    connect_wifi()

    check_internet()


    BOOT_TIME = time.time()


    debug_messages("ESP32 started")


    # 4. Main loop
    while True:
        wdt.feed() # code freeze or hang after 5 minutes reboot
        # memory cleanup
        gc.collect()
        connect_wifi()
        check_internet()
        check_temps_and_protect()
        miner_status_update()
        check_reboot_esp32_board()
        time.sleep(60)



# Auto-start
main()
