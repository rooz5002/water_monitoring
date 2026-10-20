// AFS power-consumption test firmware for LilyGo T-SIM7000G (ESP32 + SIM7000G)
//
// Re-creates the Autonomous Floating Station duty cycle described in the paper
// (wake -> battery check -> pump fill -> sense -> inference -> drain -> dry ->
//  GNSS fix -> LTE/GPRS transmit -> LDR repositioning -> deep sleep) and prints
// machine-readable markers on USB serial so a laptop script can time each phase
// against an external power meter.
//
// Serial console (115200 baud): send any key within 2 s of a reset to get the
// "afs>" prompt, then type "help". Without a key, the board starts the
// autonomous cycle (cycle -> deep sleep -> cycle ...), so it also runs on battery
// with no laptop attached.
//
// Markers (one per line, start with '@'):
//   @BOOT,<reset reason>,<ms since boot>
//   @CYCLE_BEGIN,<cycle>
//   @PHASE_BEGIN,<cycle>,<phase>,<ms>          @PHASE_END,<cycle>,<phase>,<ms>,<duration ms>
//   @CYCLE_END,<cycle>,<active ms>,<tx ok 0/1>,<gnss fix 0/1>
//   @SLEEP,<seconds>                           @STEP_READY,<name>   @STEP_END,<name>
//   @DATA,<json payload>

#include <Arduino.h>
#include <Preferences.h>
#include <esp_sleep.h>
#include <driver/gpio.h>
#include <math.h>
#include "afs_types.h"

// ---------------------------------------------------------------- pin map ---
// Fixed by the T-SIM7000G board
#define PIN_MODEM_PWRKEY 4
#define PIN_MODEM_RX     26   // ESP32 RX  <- modem TX
#define PIN_MODEM_TX     27   // ESP32 TX  -> modem RX
#define PIN_MODEM_DTR    25
#define PIN_BOARD_LED    12   // on-board LED, active LOW
#define PIN_VBAT_ADC     35   // 18650 voltage, 1:2 divider
#define PIN_SOLAR_ADC    36   // solar input voltage, 1:2 divider

// Analog sensors (ADC1 pins, safe to use while the radio is on)
#define PIN_TDS          32
#define PIN_EC           33
#define PIN_PH           34   // input-only pin
#define PIN_TURB         39   // input-only pin (VN)
// LDR pair (LDR from 3V3 to pin, 10k from pin to GND)
#define PIN_LDR_L        14
#define PIN_LDR_R        15

// Digital outputs (drive LED modules directly; pump/blower/motors via MOSFET or
// H-bridge inputs -- never connect a motor straight to a GPIO)
#define PIN_LED_RED      18
#define PIN_LED_GREEN    19
#define PIN_PUMP         21
#define PIN_BLOWER       22
#define PIN_MOTOR1       23
#define PIN_MOTOR2       5

static const int OUT_PINS[] = {PIN_LED_RED, PIN_LED_GREEN, PIN_PUMP, PIN_BLOWER,
                               PIN_MOTOR1, PIN_MOTOR2, PIN_MODEM_PWRKEY};

// ------------------------------------------------------------ configuration ---
struct Config {
  uint32_t sleep_s;     // deep sleep between cycles (paper: 1800 s)
  uint32_t fill_s;      // pump fills the chamber
  uint32_t settle_s;    // sensors settle in the sample
  uint32_t measure_s;   // ADC averaging window
  uint32_t drain_s;     // pump empties the chamber
  uint32_t dry_s;       // blower dries the chamber
  uint32_t gnss_s;      // GNSS fix timeout
  uint32_t reg_s;       // network registration timeout
  uint32_t move_s;      // repositioning motor run time
  uint32_t ncycles;     // auto mode: stop after N cycles (0 = forever)
  uint32_t cpu_mhz;     // 240 / 160 / 80
  bool use_modem;
  bool use_gnss;
  bool force_move;      // always run a motor (worst case) instead of LDR decision
  String apn;
  String host;
  uint32_t port;
  String path;
} cfg;

Preferences prefs;

static void loadConfig() {
  prefs.begin("afs", true);
  cfg.sleep_s    = prefs.getUInt("sleep", 1800);
  cfg.fill_s     = prefs.getUInt("fill", 15);
  cfg.settle_s   = prefs.getUInt("settle", 10);
  cfg.measure_s  = prefs.getUInt("measure", 5);
  cfg.drain_s    = prefs.getUInt("drain", 15);
  cfg.dry_s      = prefs.getUInt("dry", 30);
  cfg.gnss_s     = prefs.getUInt("gnss", 60);
  cfg.reg_s      = prefs.getUInt("reg", 90);
  cfg.move_s     = prefs.getUInt("move", 10);
  cfg.ncycles    = prefs.getUInt("ncycles", 0);
  cfg.cpu_mhz    = prefs.getUInt("cpu", 240);
  cfg.use_modem  = prefs.getBool("modem", true);
  cfg.use_gnss   = prefs.getBool("gnssen", true);
  cfg.force_move = prefs.getBool("force", true);
  cfg.apn        = prefs.getString("apn", "cmnet");
  cfg.host       = prefs.getString("host", "httpbin.org");
  cfg.port       = prefs.getUInt("port", 80);
  cfg.path       = prefs.getString("path", "/post");
  prefs.end();
}

static void showConfig() {
  Serial.printf("sleep=%u fill=%u settle=%u measure=%u drain=%u dry=%u gnss=%u reg=%u move=%u\n",
                cfg.sleep_s, cfg.fill_s, cfg.settle_s, cfg.measure_s, cfg.drain_s, cfg.dry_s,
                cfg.gnss_s, cfg.reg_s, cfg.move_s);
  Serial.printf("ncycles=%u cpu=%u modem=%d gnssen=%d force=%d\n", cfg.ncycles, cfg.cpu_mhz,
                cfg.use_modem, cfg.use_gnss, cfg.force_move);
  Serial.printf("apn=%s host=%s port=%u path=%s\n", cfg.apn.c_str(), cfg.host.c_str(), cfg.port,
                cfg.path.c_str());
}

static bool setConfig(const String &key, const String &val) {
  static const char *UINT_KEYS[] = {"sleep", "fill", "settle", "measure", "drain", "dry",
                                    "gnss", "reg", "move", "ncycles", "cpu", "port"};
  static const char *BOOL_KEYS[] = {"modem", "gnssen", "force"};
  static const char *STR_KEYS[] = {"apn", "host", "path"};
  prefs.begin("afs", false);
  bool ok = false;
  for (auto k : UINT_KEYS)
    if (key == k) { prefs.putUInt(k, val.toInt()); ok = true; }
  for (auto k : BOOL_KEYS)
    if (key == k) { prefs.putBool(k, val.toInt() != 0); ok = true; }
  for (auto k : STR_KEYS)
    if (key == k) { prefs.putString(k, val); ok = true; }
  prefs.end();
  if (ok) loadConfig();
  return ok;
}

// ------------------------------------------------------------ RTC state ---
enum RunMode : uint8_t { MODE_CONSOLE = 0, MODE_AUTO = 1 };
RTC_DATA_ATTR uint8_t rtcMode = MODE_CONSOLE;
RTC_DATA_ATTR uint32_t rtcCycle = 0;
RTC_DATA_ATTR uint32_t rtcCyclesDone = 0;

// ------------------------------------------------------------ helpers ---
static uint32_t curCycle = 0;
static const char *curPhase = "";
static uint32_t phaseT0 = 0;

static void phaseBegin(const char *name) {
  curPhase = name;
  phaseT0 = millis();
  Serial.printf("@PHASE_BEGIN,%u,%s,%lu\n", curCycle, name, phaseT0);
}

static void phaseEnd() {
  uint32_t now = millis();
  Serial.printf("@PHASE_END,%u,%s,%lu,%lu\n", curCycle, curPhase, now, now - phaseT0);
}

static bool serialKeyPressed() { return Serial.available() > 0; }

// Wait that can be cut short from the console (used by step mode).
static void waitMs(uint32_t ms, bool abortable = false) {
  uint32_t t0 = millis();
  while (millis() - t0 < ms) {
    if (abortable && serialKeyPressed()) return;
    delay(10);
  }
}

static float readVbat() { return analogReadMilliVolts(PIN_VBAT_ADC) * 2 / 1000.0f; }
static float readVsolar() { return analogReadMilliVolts(PIN_SOLAR_ADC) * 2 / 1000.0f; }

static float readAvgVolts(int pin, int n = 32) {
  uint32_t s = 0;
  for (int i = 0; i < n; i++) s += analogReadMilliVolts(pin);
  return s / (float)n / 1000.0f;
}

static void allOutputsOff() {
  for (int p : OUT_PINS) digitalWrite(p, LOW);
}

static void blink(int pin, int times, int ms = 100) {
  for (int i = 0; i < times; i++) {
    digitalWrite(pin, HIGH);
    delay(ms);
    digitalWrite(pin, LOW);
    delay(ms);
  }
}

// ------------------------------------------------------------ modem (AT) ---
HardwareSerial &SerialAT = Serial1;
static bool atEcho = true;  // print AT traffic to USB serial
static bool modemIsOn = false;

static bool at(const String &cmd, const char *expect = "OK", uint32_t timeout = 2000,
               String *out = nullptr) {
  while (SerialAT.available()) SerialAT.read();
  SerialAT.print(cmd);
  SerialAT.print("\r\n");
  String r;
  uint32_t t0 = millis();
  bool ok = false;
  while (millis() - t0 < timeout) {
    while (SerialAT.available()) r += (char)SerialAT.read();
    if (r.indexOf(expect) >= 0) { ok = true; break; }
    if (r.indexOf("ERROR") >= 0) break;
    delay(5);
  }
  if (atEcho) {
    String shown = r;
    shown.trim();
    shown.replace("\r\n", " | ");
    Serial.printf("  AT> %s  <- %s%s\n", cmd.c_str(), shown.c_str(), ok ? "" : "  [FAIL]");
  }
  if (out) *out = r;
  return ok;
}

static bool modemPowerOn() {
  if (modemIsOn) return true;
  pinMode(PIN_MODEM_DTR, OUTPUT);
  digitalWrite(PIN_MODEM_DTR, LOW);
  if (at("AT", "OK", 500)) { modemIsOn = true; return true; }  // already running
  for (int attempt = 0; attempt < 2; attempt++) {
    digitalWrite(PIN_MODEM_PWRKEY, HIGH);  // transistor pulls PWRKEY low
    delay(1000);
    digitalWrite(PIN_MODEM_PWRKEY, LOW);
    uint32_t t0 = millis();
    while (millis() - t0 < 10000) {
      if (at("AT", "OK", 500)) {
        at("ATE0");
        at("AT+CMEE=2");
        modemIsOn = true;
        return true;
      }
    }
  }
  Serial.println("! modem did not answer");
  return false;
}

static void modemPowerOff() {
  if (!at("AT", "OK", 500)) { modemIsOn = false; return; }
  at("AT+SGPIO=0,4,1,0");  // GNSS active-antenna power off
  if (!at("AT+CPOWD=1", "NORMAL POWER DOWN", 8000)) {
    digitalWrite(PIN_MODEM_PWRKEY, HIGH);
    delay(1500);
    digitalWrite(PIN_MODEM_PWRKEY, LOW);
  }
  modemIsOn = false;
  delay(500);
}

static bool simReady() {
  at("AT+CFUN=1", "OK", 10000);  // CFUN=0 (modem_on step) also powers the SIM down
  for (int i = 0; i < 5; i++) {
    if (at("AT+CPIN?", "READY", 2000)) return true;
    delay(1000);
  }
  return false;
}

static bool waitRegistered(uint32_t timeout_s) {
  at("AT+CFUN=1", "OK", 10000);
  at("AT+CNMP=2");   // automatic GSM / LTE
  at("AT+CMNB=3");   // Cat-M1 and NB-IoT
  uint32_t t0 = millis();
  bool saved = atEcho;
  while (millis() - t0 < timeout_s * 1000UL) {
    String r;
    atEcho = false;
    for (const char *c : {"AT+CEREG?", "AT+CGREG?", "AT+CREG?"}) {
      at(c, "OK", 1000, &r);
      if (r.indexOf(",1") >= 0 || r.indexOf(",5") >= 0) {
        atEcho = saved;
        Serial.printf("  registered via %s after %lu ms\n", c, millis() - t0);
        at("AT+CSQ");
        at("AT+COPS?");
        at("AT+CPSI?");
        return true;
      }
    }
    atEcho = saved;
    if (serialKeyPressed()) return false;
    delay(1000);
  }
  at("AT+CSQ");
  return false;
}

// Classic TCP/IP stack (works on GSM, Cat-M and NB-IoT); plain HTTP POST.
static bool httpPost(const String &json) {
  at("AT+CIPSHUT", "SHUT OK", 10000);
  at("AT+CIPMUX=0");
  if (!at("AT+CSTT=\"" + cfg.apn + "\"")) return false;
  if (!at("AT+CIICR", "OK", 60000)) return false;
  at("AT+CIFSR", ".", 5000);
  if (!at("AT+CIPSTART=\"TCP\",\"" + cfg.host + "\"," + String(cfg.port), "CONNECT OK", 30000))
    return false;
  String req = "POST " + cfg.path + " HTTP/1.1\r\nHost: " + cfg.host +
               "\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: " +
               String(json.length()) + "\r\n\r\n" + json;
  if (!at("AT+CIPSEND=" + String(req.length()), ">", 5000)) return false;
  SerialAT.print(req);
  String r;
  uint32_t t0 = millis();
  bool sent = false, acked = false;
  while (millis() - t0 < 30000) {
    while (SerialAT.available()) r += (char)SerialAT.read();
    if (!sent && r.indexOf("SEND OK") >= 0) sent = true;
    if (sent && r.indexOf("HTTP/1.") >= 0) {
      int sp = r.indexOf("HTTP/1.");
      int code = r.substring(sp + 9, sp + 12).toInt();
      acked = code >= 200 && code < 300;
      Serial.printf("  HTTP status %d\n", code);
      break;
    }
    if (r.indexOf("SEND FAIL") >= 0 || r.indexOf("CLOSED") >= 0) break;
    delay(10);
  }
  at("AT+CIPCLOSE", "OK", 3000);
  at("AT+CIPSHUT", "SHUT OK", 10000);
  return acked;
}

static bool gnssFix(uint32_t timeout_s, String &fix) {
  at("AT+SGPIO=0,4,1,1");  // power the active GNSS antenna
  at("AT+CGNSPWR=1");
  uint32_t t0 = millis();
  bool saved = atEcho, got = false;
  while (millis() - t0 < timeout_s * 1000UL) {
    String r;
    atEcho = false;
    at("AT+CGNSINF", "OK", 2000, &r);
    atEcho = saved;
    if (r.indexOf("+CGNSINF: 1,1,") >= 0) {
      int s = r.indexOf("+CGNSINF: ");
      fix = r.substring(s + 10, r.indexOf('\r', s));
      Serial.printf("  GNSS fix after %lu ms: %s\n", millis() - t0, fix.c_str());
      got = true;
      break;
    }
    if (serialKeyPressed()) break;
    delay(2000);
  }
  if (!got) Serial.printf("  no GNSS fix within %u s\n", timeout_s);
  at("AT+CGNSPWR=0");
  at("AT+SGPIO=0,4,1,0");
  return got;
}

// ------------------------------------------------------------ sensors ---
// Conversions are nominal (DFRobot application-note formulas, 25 C, 3.3 V ADC);
// this firmware is for power measurement, not for calibrated water quality.
static void convert(Sample &s) {
  float t = 25.0f, comp = 1.0f + 0.02f * (t - 25.0f), cv = s.v_tds / comp;
  s.tds_ppm = (133.42f * cv * cv * cv - 255.86f * cv * cv + 857.39f * cv) * 0.5f;
  s.ec_uscm = s.tds_ppm * 2.0f;  // 0.5 TDS factor; the EC board is read as a voltage only
  s.ph = 7.0f + (1.5f - s.v_ph) / 0.18f;
  float v5 = s.v_turb * 5.0f / 3.3f;
  s.turb_ntu = constrain(-1120.4f * v5 * v5 + 5742.3f * v5 - 4352.9f, 0.0f, 3000.0f);
}

static Sample readSensors(uint32_t window_ms) {
  Sample s{};
  double a[4] = {0, 0, 0, 0};
  uint32_t n = 0, t0 = millis();
  do {
    a[0] += readAvgVolts(PIN_TDS, 8);
    a[1] += readAvgVolts(PIN_EC, 8);
    a[2] += readAvgVolts(PIN_PH, 8);
    a[3] += readAvgVolts(PIN_TURB, 8);
    n++;
    delay(20);
  } while (millis() - t0 < window_ms);
  s.v_tds = a[0] / n; s.v_ec = a[1] / n; s.v_ph = a[2] / n; s.v_turb = a[3] / n;
  convert(s);
  return s;
}

// ------------------------------------------------------------ inference ---
// Stand-in for the on-device PSO-ASVM: an RBF-kernel SVM with the same shape
// (4 features, 3 one-vs-rest classes) and a few hundred support vectors, so the
// CPU load and duration are representative. Weights are synthetic.
#define N_SV 250
static float sv[3][N_SV][4], alpha[3][N_SV];

static void initModel() {
  uint32_t x = 12345;
  auto rnd = [&]() { x = x * 1664525u + 1013904223u; return (x >> 8) / 16777216.0f; };
  for (int c = 0; c < 3; c++)
    for (int i = 0; i < N_SV; i++) {
      for (int f = 0; f < 4; f++) sv[c][i][f] = rnd() * 2 - 1;
      alpha[c][i] = rnd() - 0.5f;
    }
}

static int classify(const Sample &s, uint32_t &us) {
  float x[4] = {s.v_ph, s.v_turb, s.v_ec, s.v_tds}, best = -1e9;
  int cls = 0;
  uint32_t t0 = micros();
  for (int c = 0; c < 3; c++) {
    float d = 0;
    for (int i = 0; i < N_SV; i++) {
      float r = 0;
      for (int f = 0; f < 4; f++) { float e = x[f] - sv[c][i][f]; r += e * e; }
      d += alpha[c][i] * expf(-0.5f * r);
    }
    if (d > best) { best = d; cls = c; }
  }
  us = micros() - t0;
  return cls;
}

// ------------------------------------------------------------ the cycle ---
static void runCycle() {
  static const char *CLASSES[] = {"Good", "Moderate", "Poor"};
  curCycle = ++rtcCycle;
  uint32_t tStart = millis();
  Serial.printf("@CYCLE_BEGIN,%u\n", curCycle);
  digitalWrite(PIN_LED_GREEN, HIGH);  // status: active

  phaseBegin("wake_check");
  float vbat = readVbat(), vsol = readVsolar();
  Serial.printf("  Vbat=%.2f V  Vsolar=%.2f V\n", vbat, vsol);
  phaseEnd();

  phaseBegin("pump_fill");
  digitalWrite(PIN_PUMP, HIGH);
  waitMs(cfg.fill_s * 1000);
  digitalWrite(PIN_PUMP, LOW);
  phaseEnd();

  phaseBegin("sense");
  waitMs(cfg.settle_s * 1000);
  Sample s = readSensors(cfg.measure_s * 1000);
  s.v_ldr_l = readAvgVolts(PIN_LDR_L);
  s.v_ldr_r = readAvgVolts(PIN_LDR_R);
  Serial.printf("  V: tds=%.3f ec=%.3f ph=%.3f turb=%.3f ldrL=%.3f ldrR=%.3f\n", s.v_tds, s.v_ec,
                s.v_ph, s.v_turb, s.v_ldr_l, s.v_ldr_r);
  phaseEnd();

  phaseBegin("inference");
  uint32_t us;
  int cls = classify(s, us);
  Serial.printf("  class=%s  inference=%lu us\n", CLASSES[cls], us);
  phaseEnd();

  phaseBegin("drain");
  digitalWrite(PIN_PUMP, HIGH);
  waitMs(cfg.drain_s * 1000);
  digitalWrite(PIN_PUMP, LOW);
  phaseEnd();

  phaseBegin("dry_blower");
  digitalWrite(PIN_BLOWER, HIGH);
  waitMs(cfg.dry_s * 1000);
  digitalWrite(PIN_BLOWER, LOW);
  phaseEnd();

  bool fixOk = false, txOk = false;
  String fix = "";
  if (cfg.use_modem) {
    phaseBegin("modem_on");
    bool on = modemPowerOn();
    phaseEnd();

    if (on && cfg.use_gnss) {
      phaseBegin("gnss");
      fixOk = gnssFix(cfg.gnss_s, fix);
      phaseEnd();
    }

    if (on) {
      phaseBegin("transmit");
      char json[320];
      snprintf(json, sizeof(json),
               "{\"id\":%u,\"uptime_ms\":%lu,\"gnss\":\"%s\",\"ph\":%.2f,\"turb\":%.1f,"
               "\"ec\":%.1f,\"tds\":%.1f,\"vbat\":%.2f,\"class\":\"%s\"}",
               curCycle, millis(), fixOk ? fix.substring(0, 60).c_str() : "", s.ph, s.turb_ntu,
               s.ec_uscm, s.tds_ppm, vbat, CLASSES[cls]);
      Serial.printf("@DATA,%s\n", json);
      if (!simReady()) {
        Serial.println("! no SIM / SIM not ready");
      } else if (waitRegistered(cfg.reg_s)) {
        for (int attempt = 1; attempt <= 5 && !txOk; attempt++) {  // paper: up to 5 attempts
          Serial.printf("  POST attempt %d\n", attempt);
          txOk = httpPost(json);
        }
      } else {
        Serial.println("! not registered on network");
      }
      phaseEnd();
    }

    phaseBegin("modem_off");
    modemPowerOff();
    phaseEnd();
  }

  phaseBegin("reposition");
  float dv = readAvgVolts(PIN_LDR_L) - readAvgVolts(PIN_LDR_R);
  int motor = cfg.force_move ? PIN_MOTOR1 : (dv > 0.2f ? PIN_MOTOR1 : (dv < -0.2f ? PIN_MOTOR2 : -1));
  Serial.printf("  dV(LDR)=%.3f V -> %s\n", dv,
                motor == PIN_MOTOR1 ? "motor1" : motor == PIN_MOTOR2 ? "motor2" : "stay");
  if (motor >= 0) {
    digitalWrite(motor, HIGH);
    waitMs(cfg.move_s * 1000);
    digitalWrite(motor, LOW);
  }
  phaseEnd();

  digitalWrite(PIN_LED_GREEN, LOW);
  blink(txOk ? PIN_LED_GREEN : PIN_LED_RED, 2);
  Serial.printf("@CYCLE_END,%u,%lu,%d,%d\n", curCycle, millis() - tStart, txOk, fixOk);
}

// ------------------------------------------------------------ deep sleep ---
static void deepSleep(uint32_t seconds) {
  if (modemIsOn || at("AT", "OK", 300)) modemPowerOff();
  allOutputsOff();
  for (int p : OUT_PINS) gpio_hold_en((gpio_num_t)p);
  gpio_deep_sleep_hold_en();
  esp_sleep_enable_timer_wakeup((uint64_t)seconds * 1000000ULL);
  Serial.printf("@SLEEP,%u\n", seconds);
  Serial.flush();
  esp_deep_sleep_start();
}

// ------------------------------------------------------------ step mode ---
// Holds one subsystem in a steady state until any key arrives, so a meter can
// be read for the per-subsystem breakdown.
static void step(const String &name) {
  int pin = -1;
  if (name == "ledr") pin = PIN_LED_RED;
  else if (name == "ledg") pin = PIN_LED_GREEN;
  else if (name == "pump") pin = PIN_PUMP;
  else if (name == "blower") pin = PIN_BLOWER;
  else if (name == "m1") pin = PIN_MOTOR1;
  else if (name == "m2") pin = PIN_MOTOR2;

  allOutputsOff();
  if (pin >= 0) {
    digitalWrite(pin, HIGH);
    Serial.printf("@STEP_READY,%s\n", name.c_str());
    while (!serialKeyPressed()) delay(10);
    digitalWrite(pin, LOW);
  } else if (name == "idle") {  // ESP32 awake, radios off, modem powered down
    modemPowerOff();
    Serial.printf("@STEP_READY,%s\n", name.c_str());
    while (!serialKeyPressed()) delay(10);
  } else if (name == "cpu") {  // ESP32 running inference flat out
    Serial.printf("@STEP_READY,%s\n", name.c_str());
    Sample s{};
    uint32_t us;
    while (!serialKeyPressed()) classify(s, us);
  } else if (name == "sensors") {  // continuous ADC sampling
    Serial.printf("@STEP_READY,%s\n", name.c_str());
    while (!serialKeyPressed()) readSensors(200);
  } else if (name == "modem_on") {  // powered, RF off (CFUN=0)
    if (modemPowerOn()) at("AT+CFUN=0", "OK", 10000);
    Serial.printf("@STEP_READY,%s\n", name.c_str());
    while (!serialKeyPressed()) delay(10);
  } else if (name == "gnss") {  // GNSS searching / tracking
    if (modemPowerOn()) {
      at("AT+SGPIO=0,4,1,1");
      at("AT+CGNSPWR=1");
    }
    Serial.printf("@STEP_READY,%s\n", name.c_str());
    while (!serialKeyPressed()) delay(10);
    at("AT+CGNSPWR=0");
    at("AT+SGPIO=0,4,1,0");
  } else if (name == "modem_reg") {  // registered on network, idle
    bool ok = modemPowerOn() && simReady() && waitRegistered(cfg.reg_s);
    Serial.printf("@STEP_READY,%s,%s\n", name.c_str(), ok ? "registered" : "NOT_registered");
    while (!serialKeyPressed()) delay(10);
  } else if (name == "modem_tx") {  // back-to-back HTTP POSTs
    bool ok = modemPowerOn() && simReady() && waitRegistered(cfg.reg_s);
    Serial.printf("@STEP_READY,%s,%s\n", name.c_str(), ok ? "registered" : "NOT_registered");
    uint32_t n = 0, good = 0;
    while (ok && !serialKeyPressed()) {
      n++;
      if (httpPost("{\"test\":\"afs_power\",\"n\":" + String(n) + "}")) good++;
      Serial.printf("  posts %u ok %u\n", n, good);
    }
    while (!serialKeyPressed()) delay(10);
  } else {
    Serial.println("unknown step; try: idle cpu sensors ledr ledg pump blower m1 m2 "
                   "modem_on gnss modem_reg modem_tx");
    return;
  }
  Serial.printf("@STEP_END,%s\n", name.c_str());
  while (Serial.available()) Serial.read();  // swallow the key that ended the step
}

// ------------------------------------------------------------ console ---
static void help() {
  Serial.println(
      "commands:\n"
      "  show                    print configuration\n"
      "  set <key> <value>       keys: sleep fill settle measure drain dry gnss reg move\n"
      "                          ncycles cpu modem gnssen force apn host port path\n"
      "  cycle                   run one full cycle now, stay awake\n"
      "  auto                    cycle -> deep sleep(sleep) -> cycle ... (ncycles, 0=forever)\n"
      "  step <name>             hold one subsystem on until any key\n"
      "  sleep <s>               deep sleep for s seconds, then back to this console\n"
      "  modem on|off            power the SIM7000G up/down\n"
      "  at <command>            send a raw AT command\n"
      "  echo on|off             show AT traffic\n"
      "  vbat                    battery and solar voltage\n"
      "  read [n]                print all analog inputs n times (1 s apart)");
}

static void handle(String line) {
  line.trim();
  if (!line.length()) return;
  int sp = line.indexOf(' ');
  String cmd = sp < 0 ? line : line.substring(0, sp);
  String arg = sp < 0 ? "" : line.substring(sp + 1);
  arg.trim();

  if (cmd == "help") help();
  else if (cmd == "show") showConfig();
  else if (cmd == "set") {
    int s2 = arg.indexOf(' ');
    if (s2 < 0 || !setConfig(arg.substring(0, s2), arg.substring(s2 + 1)))
      Serial.println("usage: set <key> <value>");
    else {
      setCpuFrequencyMhz(cfg.cpu_mhz);
      showConfig();
    }
  } else if (cmd == "cycle") runCycle();
  else if (cmd == "auto") {
    rtcMode = MODE_AUTO;
    rtcCyclesDone = 0;
    runCycle();
    rtcCyclesDone++;
    if (cfg.ncycles && rtcCyclesDone >= cfg.ncycles) {
      rtcMode = MODE_CONSOLE;
      Serial.println("@AUTO_DONE");
    } else deepSleep(cfg.sleep_s);
  } else if (cmd == "step") step(arg);
  else if (cmd == "sleep") deepSleep(arg.toInt() > 0 ? arg.toInt() : 30);
  else if (cmd == "modem") arg == "off" ? modemPowerOff() : (void)modemPowerOn();
  else if (cmd == "at") at(arg, "OK", 10000);
  else if (cmd == "echo") atEcho = arg != "off";
  else if (cmd == "read") {
    int n = arg.toInt() > 0 ? arg.toInt() : 1;
    for (int i = 0; i < n && !(i && serialKeyPressed()); i++) {
      Serial.printf("@READ,tds=%.3f,ec=%.3f,ph=%.3f,turb=%.3f,ldrL=%.3f,ldrR=%.3f,vbat=%.2f\n",
                    readAvgVolts(PIN_TDS, 64), readAvgVolts(PIN_EC, 64), readAvgVolts(PIN_PH, 64),
                    readAvgVolts(PIN_TURB, 64), readAvgVolts(PIN_LDR_L, 64),
                    readAvgVolts(PIN_LDR_R, 64), readVbat());
      if (i + 1 < n) waitMs(1000, true);
    }
  } else if (cmd == "vbat") Serial.printf("Vbat=%.2f V  Vsolar=%.2f V\n", readVbat(), readVsolar());
  else Serial.println("unknown command, type help");
}

// ------------------------------------------------------------ main ---
void setup() {
  Serial.begin(115200);
  gpio_deep_sleep_hold_dis();
  for (int p : OUT_PINS) {
    gpio_hold_dis((gpio_num_t)p);
    pinMode(p, OUTPUT);
    digitalWrite(p, LOW);
  }
  pinMode(PIN_BOARD_LED, OUTPUT);
  digitalWrite(PIN_BOARD_LED, HIGH);  // keep the on-board LED off
  analogSetAttenuation(ADC_11db);
  // Open the modem UART now so modemPowerOff() can reach a modem left running
  // by a previous session (an ESP32 reset does not power the modem down).
  SerialAT.begin(115200, SERIAL_8N1, PIN_MODEM_RX, PIN_MODEM_TX);
  loadConfig();
  setCpuFrequencyMhz(cfg.cpu_mhz);
  initModel();

  esp_sleep_wakeup_cause_t why = esp_sleep_get_wakeup_cause();
  Serial.printf("@BOOT,%s,%lu\n", why == ESP_SLEEP_WAKEUP_TIMER ? "timer" : "reset", millis());

  if (why == ESP_SLEEP_WAKEUP_TIMER) {
    if (rtcMode == MODE_AUTO) {
      runCycle();
      rtcCyclesDone++;
      if (cfg.ncycles && rtcCyclesDone >= cfg.ncycles) {
        rtcMode = MODE_CONSOLE;
        Serial.println("@AUTO_DONE");
      } else deepSleep(cfg.sleep_s);
    }
  } else {
    Serial.println("AFS power test firmware - press any key within 2 s for the console");
    uint32_t t0 = millis();
    while (millis() - t0 < 2000 && !serialKeyPressed()) delay(10);
    if (!serialKeyPressed()) {  // unattended power-up: run autonomously
      rtcCycle = 0;
      handle("auto");
    }
    while (Serial.available()) Serial.read();
  }
  rtcMode = MODE_CONSOLE;
  help();
  Serial.print("afs> ");
}

void loop() {
  static String line;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      Serial.println(line);
      handle(line);
      line = "";
      Serial.print("afs> ");
    } else line += c;
  }
  delay(5);
}
