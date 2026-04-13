import network
import urequests
import utime as time
from machine import Pin, WDT
import usocket
import gc
from config import cfg
import machine

# ===================== CONFIG =====================
DEBUG = True

WIFI_SSID = cfg["WIFI_SSID"]
WIFI_PASS = cfg["WIFI_PASS"]





#FIREBASE_RELAY_URL = "https://gopal-8d955.firebaseio.com/hub/relay.json"
FIREBASE_RELAY_URL = "https://gopal-8d955.firebaseio.com/" + cfg["HUB"] + "/relay.json"


REBOOT_URL = "https://gopal-8d955.firebaseio.com/" + cfg["HUB"] + "/reboot_relay_esp32_board.json"

# FireBase Storage files

REBOOT_URL = ""
if cfg["ESP32_Board"] == "relay_board":
    REBOOT_URL = "https://gopal-8d955.firebaseio.com/" + cfg["HUB"] + "/reboot_relay_esp32_board.json"
elif cfg["ESP32_Board"] == "status_board":
    REBOOT_URL = "https://gopal-8d955.firebaseio.com/" + cfg["HUB"] + "/reboot_status_esp32_board.json"
else:
    print("ESP32_Board TYPE Not Found")


RELAY_ACTIVE_HIGH = False   # False = active LOW relay boards

def safe_sleep(seconds):
    for _ in range(seconds // 5):
        wdt.feed()
        time.sleep(5)

# Global rest cycle
REST_CYCLE_HOURS = 10
REST_DURATION_MINUTES = 30

# ===================== WATCHDOG =====================
wdt = WDT(timeout=300000)  # 5 minutes

# ===================== DEBUG =====================
def debug(msg):
    if DEBUG:
        print(msg)
def debug_messages(message_data):
    if(DEBUG == True):
        print(str(message_data))

# ===================== RELAY PINS =====================



relay_77 = Pin(5, Pin.OUT, value=1)
relay_78 = Pin(18, Pin.OUT, value=1)
relay_79 = Pin(19, Pin.OUT, value=1)

RELAYS = {
    "77": relay_77,
    "78": relay_78,
    "79": relay_79
}

last_applied_state = {
    "77": None,
    "78": None,
    "79": None
}



# AntMiner Api Acess ===================================
MINER_IP_BASE = "192.168.1."
MINER_USER = "root"
MINER_PASS = "root"
def miner_sleep_mode(relay_id):
    miner_ip = MINER_IP_BASE + relay_id
    url = "http://{}/cgi-bin/set_miner_conf.cgi".format(miner_ip)

    debug("Miner {} → SLEEP".format(miner_ip))

    payload = {
        "bitmain-fan-ctrl": False,
        "bitmain-fan-pwm": "100",
        "bitmain-hashrate-percent": "100",
        "miner-mode": 1
    }

    try:
        r = urequests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            auth=(MINER_USER, MINER_PASS)
        )
        r.close()
    except:
        pass  # timeout normal
def miner_normal_mode(relay_id):
    miner_ip = MINER_IP_BASE + relay_id
    url = "http://{}/cgi-bin/set_miner_conf.cgi".format(miner_ip)

    debug("Miner {} → NORMAL".format(miner_ip))

    payload = {
        "bitmain-fan-ctrl": False,
        "bitmain-fan-pwm": "100",
        "bitmain-hashrate-percent": "100",
        "miner-mode": 0
    }

    try:
        r = urequests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            auth=(MINER_USER, MINER_PASS)
        )
        r.close()
    except:
        pass  # timeout normal
def miner_reboot(relay_id):
    miner_ip = MINER_IP_BASE + relay_id
    url = "http://{}/cgi-bin/reboot.cgi".format(miner_ip)

    debug("Miner {} → REBOOT".format(miner_ip))

    try:
        r = urequests.get(
            url,
            auth=(MINER_USER, MINER_PASS)
        )
        r.close()
    except:
        pass




# ===================== RELAY CONTROL =====================
def relay_on(rid, pin):

    pin.value(0)
    safe_sleep(60)
    miner_reboot(str(rid))
    debug("releay on - " + str(pin))

def relay_off(rid , pin):

    pin.value(1)
    debug("releay off - " + str(pin))


def all_relays_on():
    for rid, pin in RELAYS.items():
        relay_on(rid, pin)



def all_relays_off():
    for rid, pin in RELAYS.items():
        relay_off(rid, pin)

# Safe boot OFF
all_relays_off()

# ===================== WIFI =====================
wlan = None

def connect_wifi():
    global wlan

    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        wlan.config(pm=network.WLAN.PM_NONE)

    if wlan.isconnected():
        return True

    debug("Connecting WiFi...")
    wlan.connect(WIFI_SSID, WIFI_PASS)

    for _ in range(15):
        if wlan.isconnected():
            debug("WiFi OK: " + str(wlan.ifconfig()))
            return True
        time.sleep(1)

    debug("WiFi FAILED")
    return False

# ===================== INTERNET CHECK =====================
def check_internet():
    try:
        s = usocket.socket()
        s.settimeout(5)
        s.connect(("8.8.8.8", 53))
        s.close()
        return True
    except:
        return False

# ===================== FIREBASE STATE =====================
firebase_state = {
    "77": "off",
    "78": "off",
    "79": "off"
}

def firebase_relay_updates_get():
    global firebase_state
    try:
        r = urequests.get(FIREBASE_RELAY_URL)
        data = r.json()
        r.close()

        for rid, state in data.items():
            if rid in firebase_state:
                firebase_state[rid] = state
                debug("Firebase {} -> {}".format(rid, state))

    except Exception as e:
        debug("Firebase error: " + str(e))

# ===================== GLOBAL DAILY REST =====================
_last_rest_ts = None
_in_rest = False

def daily_rest_cycle():
    global _last_rest_ts, _in_rest

    now = time.time()

    # init
    if _last_rest_ts is None:
        _last_rest_ts = now
        return

    elapsed = now - _last_rest_ts

    # ---- START REST ----
    if not _in_rest and elapsed >= REST_CYCLE_HOURS * 3600:
        all_relays_off()
        _in_rest = True
        _last_rest_ts = now
        debug("GLOBAL REST START (ALL RELAYS OFF)")
        return

    # ---- END REST ----
    if _in_rest and elapsed >= REST_DURATION_MINUTES * 60:
        _in_rest = False
        _last_rest_ts = now
        # 🔑 CRITICAL LINE
        for rid in last_applied_state:
            last_applied_state[rid] = None
        debug("GLOBAL REST END")

# ===================== APPLY FIREBASE =====================
def apply_firebase_state():
    if _in_rest:
        return  # rest overrides everything

    for rid, pin in RELAYS.items():
        desired = firebase_state[rid]
        last = last_applied_state[rid]

        # Only update if state changed
        if desired != last:
            if desired == "on":
                relay_on(rid, pin)
                debug("Relay {} ON".format(rid))
            else:
                relay_off(rid, pin)
                debug("Relay {} OFF".format(rid))

            last_applied_state[rid] = desired


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

# ===================== MAIN LOOP =====================
def main():
    connect_wifi()

    while True:
        wdt.feed()
        gc.collect()

        if not wlan.isconnected():
            connect_wifi()

        if check_internet():
            firebase_relay_updates_get()

        daily_rest_cycle()
        apply_firebase_state()

        check_reboot_esp32_board()

        safe_sleep(60)

# ===================== START =====================
main()


