#include <WiFi.h>
#include <WebServer.h>
#include <ESP32Servo.h>

#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

const char* ssid = "Robot";
const char* password = "12345678";

IPAddress local_IP(10, 42, 0, 224);
IPAddress gateway(10, 42, 0, 1);
IPAddress subnet(255, 255, 255, 0);

WebServer server(80);

Servo baseServo;
Servo shoulderServo;
Servo elbowServo;
Servo gripperServo;

#define BASE_PIN 13
#define SHOULDER_PIN 12
#define ELBOW_PIN 14
#define GRIPPER_PIN 27

#define GRIPPER_OPEN 45
#define GRIPPER_CLOSE 130

#define BIN_REJECT_BASE 180     // ← أقصى اليمين (0°=يسار, 180°=يمين)
#define BIN_REJECT_SHOULDER 10    // ← أقصى الشمال/أعلى (10° قريب من الأعلى)
#define BIN_REJECT_ELBOW 170      // ← مرفق ممتد للوصول للأعلى
#define BIN_ACCEPT_BASE 150       // ← يمين (صندوق القبول)
#define BIN_ACCEPT_SHOULDER 120   // ← ارتفاع معتدل
#define BIN_ACCEPT_ELBOW 100      // ← مرفق منخفض

// ===== قيم الرفع الآمن المعدلة =====
#define SAFE_SHOULDER 150     // ← رفع الكتف للأعلى (كان 110)
#define SAFE_ELBOW 50         // ← رفع المرفق للأعلى (كان 90)

// ===== قيم رفع إضافية =====
#define LIFT_SHOULDER 155     // رفع كامل للحركات الطويلة
#define LIFT_ELBOW 45         // رفع كامل للمرفق

int currentBase = 90;
int currentShoulder = 90;
int currentElbow = 90;
int currentGripper = 90;

#define SMOOTH_DELAY_FAST 15
#define SMOOTH_DELAY_NORMAL 20
#define SMOOTH_DELAY_SLOW 30

// ===== متغير يشير أن الذراع مشغول =====
bool armBusy = false;

void setup() {
  Serial.begin(115200);
  delay(1000);

  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);

  Serial.println("\n🤖 ESP32 Robot Arm Controller");
  Serial.println("==============================");

  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);

  baseServo.setPeriodHertz(50);
  baseServo.attach(BASE_PIN, 500, 2400);
  shoulderServo.setPeriodHertz(50);
  shoulderServo.attach(SHOULDER_PIN, 500, 2400);
  elbowServo.setPeriodHertz(50);
  elbowServo.attach(ELBOW_PIN, 500, 2400);
  gripperServo.setPeriodHertz(50);
  gripperServo.attach(GRIPPER_PIN, 500, 2400);

  Serial.println("✅ Servos attached");

  baseServo.write(90);
  shoulderServo.write(90);
  elbowServo.write(90);
  gripperServo.write(90);
  delay(500);
  Serial.println("✅ Home position set");

  Serial.print("📡 WiFi connecting... ");
  if (!WiFi.config(local_IP, gateway, subnet)) {
    Serial.println("❌ Static IP failed!");
  } else {
    Serial.println("✅ Static IP: 10.42.0.224");
  }

  WiFi.begin(ssid, password);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 40) {
    delay(500);
    Serial.print(".");
    attempts++;
    if (attempts % 10 == 0) {
      Serial.println("\n🔄 Retrying...");
      WiFi.disconnect();
      delay(1000);
      WiFi.begin(ssid, password);
    }
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n✅ WiFi Connected!");
    Serial.print("🌐 IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\n❌ WiFi failed!");
  }

  server.on("/sort", HTTP_GET, handleSort);
  server.on("/status", HTTP_GET, handleStatus);
  server.on("/home", HTTP_GET, handleHome);
  server.on("/testlift", HTTP_GET, handleTestLift);
  server.on("/emergency", HTTP_GET, handleEmergency);

  server.onNotFound([]() {
    server.send(404, "application/json", "{\"status\":\"error\",\"message\":\"Not Found\"}");
  });

  server.begin();
  Serial.println("🚀 Server started on port 80");
  Serial.println("==============================\n");
}

void loop() {
  server.handleClient();

  if (WiFi.status() != WL_CONNECTED) {
    static unsigned long lastReconnectAttempt = 0;
    if (millis() - lastReconnectAttempt > 10000) {
      lastReconnectAttempt = millis();
      Serial.println("🔄 WiFi reconnecting...");
      WiFi.disconnect();
      WiFi.begin(ssid, password);
    }
  }
}

// ===== Home المباشر =====
void homePosition() {
  Serial.println("🏠 Home...");
  moveServoSmooth(baseServo, currentBase, 90, SMOOTH_DELAY_NORMAL);
  moveServoSmooth(shoulderServo, currentShoulder, 90, SMOOTH_DELAY_NORMAL);
  moveServoSmooth(elbowServo, currentElbow, 90, SMOOTH_DELAY_NORMAL);
  moveServoSmooth(gripperServo, currentGripper, 90, SMOOTH_DELAY_NORMAL);
  currentBase = 90;
  currentShoulder = 90;
  currentElbow = 90;
  currentGripper = 90;
  delayWithServer(300);
  Serial.println("✅ Home reached");
}

// ===== رفع الذراع بشكل آمن =====
void liftArmSafely() {
  Serial.println("🆙 Lifting arm step by step...");
  
  // الخطوة 1: رفع المرفق أولاً
  moveServoSmooth(elbowServo, currentElbow, 60, SMOOTH_DELAY_FAST);
  currentElbow = 60;
  delayWithServer(200);
  
  // الخطوة 2: رفع الكتف
  moveServoSmooth(shoulderServo, currentShoulder, 130, SMOOTH_DELAY_FAST);
  currentShoulder = 130;
  delayWithServer(200);
  
  // الخطوة 3: رفع إضافي للوصول للوضع الآمن
  moveServoSmooth(elbowServo, currentElbow, SAFE_ELBOW, SMOOTH_DELAY_FAST);
  moveServoSmooth(shoulderServo, currentShoulder, SAFE_SHOULDER, SMOOTH_DELAY_FAST);
  currentElbow = SAFE_ELBOW;
  currentShoulder = SAFE_SHOULDER;
  delayWithServer(300);
  
  Serial.printf("   ✅ Arm fully lifted: Shoulder=%d°, Elbow=%d°\n", currentShoulder, currentElbow);
}

// ===== Home الآمن =====
void safeHomePosition() {
  Serial.println("🏠 Safe Home...");
  
  // الخطوة 1: رفع الذراع أعلى بكثير
  Serial.println("   ⬆️ Step 1: Lifting arm HIGH...");
  moveServoSmooth(shoulderServo, currentShoulder, SAFE_SHOULDER, SMOOTH_DELAY_NORMAL);
  moveServoSmooth(elbowServo, currentElbow, SAFE_ELBOW, SMOOTH_DELAY_NORMAL);
  currentShoulder = SAFE_SHOULDER;
  currentElbow = SAFE_ELBOW;
  delayWithServer(500);
  
  // تأكد من وصول الذراع للارتفاع المطلوب
  Serial.printf("   📏 Arm height: Shoulder=%d°, Elbow=%d°\n", currentShoulder, currentElbow);

  // الخطوة 2: دوران القاعدة
  Serial.println("   🔄 Step 2: Rotating base...");
  moveServoSmooth(baseServo, currentBase, 90, SMOOTH_DELAY_NORMAL);
  currentBase = 90;
  delayWithServer(500);

  // الخطوة 3: العودة للوضع المنخفض
  Serial.println("   ⬇️ Step 3: Lowering to home...");
  moveServoSmooth(shoulderServo, currentShoulder, 90, SMOOTH_DELAY_NORMAL);
  moveServoSmooth(elbowServo, currentElbow, 90, SMOOTH_DELAY_NORMAL);
  moveServoSmooth(gripperServo, currentGripper, 90, SMOOTH_DELAY_FAST);
  currentShoulder = 90;
  currentElbow = 90;
  currentGripper = 90;
  delayWithServer(300);
  
  Serial.println("✅ Safe Home reached");
}

// ===== delay مع معالجة الخادم =====
void delayWithServer(unsigned long ms) {
  unsigned long start = millis();
  while (millis() - start < ms) {
    server.handleClient();  // ← معالجة الطلبات أثناء الانتظار!
    delay(1);
  }
}

// ===== معالجة طلب الفرز =====
void handleSort() {
  if (armBusy) {
    server.send(409, "application/json", "{\"status\":\"busy\",\"message\":\"Arm is busy\"}");
    Serial.println("⚠️ Sort request rejected - arm busy");
    return;
  }

  if (!server.hasArg("bin") || !server.hasArg("x") || !server.hasArg("y")) {
    server.send(400, "application/json", 
      "{\"status\":\"error\",\"message\":\"Missing parameters: bin, x, y\"}");
    return;
  }

  int bin = server.arg("bin").toInt();
  int x = server.arg("x").toInt();
  int y = server.arg("y").toInt();

  Serial.println("\n📥 ==============================");
  Serial.printf("📥 SORT: bin=%d, x=%d, y=%d\n", bin, x, y);
  Serial.println("📥 ==============================");

  armBusy = true;
  bool success = pickAndPlaceSequence(bin, x, y);
  armBusy = false;

  if (success) {
    String json = "{\"status\":\"ok\",\"bin\":" + String(bin) + 
                  ",\"x\":" + String(x) + ",\"y\":" + String(y) + 
                  ",\"time\":5.0,\"message\":\"Sort completed\"}";
    server.send(200, "application/json", json);
    Serial.println("📤 200 OK");
  } else {
    server.send(500, "application/json", 
      "{\"status\":\"error\",\"message\":\"Sort failed\"}");
    Serial.println("📤 500 ERROR");
  }
}

void handleStatus() {
  String json = "{\"status\":\"ok\",\"armBusy\":" + String(armBusy ? "true" : "false") + 
                ",\"base\":" + String(currentBase) + 
                ",\"shoulder\":" + String(currentShoulder) + 
                ",\"elbow\":" + String(currentElbow) + 
                ",\"gripper\":" + String(currentGripper) + "}";
  server.send(200, "application/json", json);
}

void handleHome() {
  if (armBusy) {
    server.send(409, "application/json", "{\"status\":\"busy\",\"message\":\"Arm is busy\"}");
    return;
  }
  safeHomePosition();
  server.send(200, "application/json", "{\"status\":\"ok\",\"message\":\"Home\"}");
}

void handleTestLift() {
  if (armBusy) {
    server.send(409, "application/json", "{\"status\":\"busy\"}");
    return;
  }
  
  armBusy = true;
  
  Serial.println("🧪 Testing arm lift...");
  
  // اختبار رفع الذراع
  Serial.println("Lifting to SAFE position...");
  moveServoSmooth(shoulderServo, currentShoulder, SAFE_SHOULDER, SMOOTH_DELAY_NORMAL);
  moveServoSmooth(elbowServo, currentElbow, SAFE_ELBOW, SMOOTH_DELAY_NORMAL);
  currentShoulder = SAFE_SHOULDER;
  currentElbow = SAFE_ELBOW;
  
  delayWithServer(2000);  // انتظر ثانيتين في الوضع المرتفع
  
  Serial.println("Returning to home...");
  moveServoSmooth(shoulderServo, currentShoulder, 90, SMOOTH_DELAY_NORMAL);
  moveServoSmooth(elbowServo, currentElbow, 90, SMOOTH_DELAY_NORMAL);
  currentShoulder = 90;
  currentElbow = 90;
  
  armBusy = false;
  server.send(200, "application/json", "{\"status\":\"lift_tested\"}");
  
  Serial.println("✅ Lift test complete");
}

void handleEmergency() {
  armBusy = true;  // قفل أي حركة جديدة
  
  // إيقاف جميع السيرفات فوراً
  baseServo.detach();
  shoulderServo.detach();
  elbowServo.detach();
  gripperServo.detach();
  
  server.send(200, "application/json", "{\"status\":\"emergency_stop\"}");
  Serial.println("🚨 EMERGENCY STOP ACTIVATED!");
  
  // إعادة تشغيل السيرفات بعد 5 ثواني
  delayWithServer(5000);
  
  baseServo.attach(BASE_PIN, 500, 2400);
  shoulderServo.attach(SHOULDER_PIN, 500, 2400);
  elbowServo.attach(ELBOW_PIN, 500, 2400);
  gripperServo.attach(GRIPPER_PIN, 500, 2400);
  
  armBusy = false;
  Serial.println("✅ Servos reattached");
}

// ===== حساب الزوايا =====
void calculateAngles(int x, int y, int &baseAngle, int &shoulderAngle, int &elbowAngle) {
  // تحويل إحداثيات الشاشة (640x480) إلى زوايا حقيقية
  
  // القاعدة: 0-180 مع منطقة ميتة في المنتصف
  baseAngle = map(x, 0, 640, 0, 180);
  baseAngle = constrain(baseAngle, 0, 180);
  
  // الكتف والمرفق: علاقة عكسية للحفاظ على الاستقرار
  int normalizedY = constrain(y, 0, 480);
  
  // منطقة آمنة للعمل (تجنب التصادم)
  if (normalizedY < 100) {
    // منطقة عليا - رفع الذراع
    shoulderAngle = map(normalizedY, 0, 100, 140, 110);
    elbowAngle = map(normalizedY, 0, 100, 50, 70);
  } else if (normalizedY > 380) {
    // منطقة سفلى - خفض الذراع
    shoulderAngle = map(normalizedY, 380, 480, 80, 50);
    elbowAngle = map(normalizedY, 380, 480, 120, 140);
  } else {
    // منطقة وسطى - عمل طبيعي
    shoulderAngle = map(normalizedY, 100, 380, 110, 80);
    elbowAngle = map(normalizedY, 100, 380, 70, 120);
  }
  
  shoulderAngle = constrain(shoulderAngle, 50, 160);
  elbowAngle = constrain(elbowAngle, 40, 160);

  Serial.printf("🧮 Angles: Base=%d°, Shoulder=%d°, Elbow=%d°\n", 
                baseAngle, shoulderAngle, elbowAngle);
}

// ===== سلسلة الالتقاط والفرز =====
bool pickAndPlaceSequence(int bin, int x, int y) {
  // التحقق من صحة المعاملات
  if (bin != 0 && bin != 1) {
    Serial.println("❌ Invalid bin number!");
    return false;
  }
  
  if (x < 0 || x > 640 || y < 0 || y > 480) {
    Serial.println("❌ Invalid coordinates!");
    return false;
  }
  
  int targetBase, targetShoulder, targetElbow;
  calculateAngles(x, y, targetBase, targetShoulder, targetElbow);

  Serial.println("🔓 [1/9] Opening gripper...");
  moveServoSmooth(gripperServo, currentGripper, GRIPPER_OPEN, SMOOTH_DELAY_FAST);
  currentGripper = GRIPPER_OPEN;
  delayWithServer(400);

  Serial.println("🎯 [2/9] Moving to bottle...");
  moveServoSmooth(baseServo, currentBase, targetBase, SMOOTH_DELAY_NORMAL);
  currentBase = targetBase;
  delayWithServer(200);

  moveServoSmooth(shoulderServo, currentShoulder, targetShoulder, SMOOTH_DELAY_NORMAL);
  moveServoSmooth(elbowServo, currentElbow, targetElbow, SMOOTH_DELAY_NORMAL);
  currentShoulder = targetShoulder;
  currentElbow = targetElbow;
  delayWithServer(600);

  Serial.println("⬇️ [3/9] Lowering to grab...");
  int grabShoulder = targetShoulder + 15;
  int grabElbow = targetElbow - 15;
  grabShoulder = constrain(grabShoulder, 50, 160);
  grabElbow = constrain(grabElbow, 40, 160);

  moveServoSmooth(shoulderServo, currentShoulder, grabShoulder, SMOOTH_DELAY_SLOW);
  moveServoSmooth(elbowServo, currentElbow, grabElbow, SMOOTH_DELAY_SLOW);
  currentShoulder = grabShoulder;
  currentElbow = grabElbow;
  delayWithServer(500);

  Serial.println("🔒 [4/9] Closing gripper...");
  moveServoSmooth(gripperServo, currentGripper, GRIPPER_CLOSE, SMOOTH_DELAY_FAST);
  currentGripper = GRIPPER_CLOSE;
  delayWithServer(800);

  Serial.println("⬆️ [5/9] Lifting arm HIGH for rotation...");
  // استخدم قيم رفع أعلى
  moveServoSmooth(shoulderServo, currentShoulder, SAFE_SHOULDER, SMOOTH_DELAY_NORMAL);
  moveServoSmooth(elbowServo, currentElbow, SAFE_ELBOW, SMOOTH_DELAY_NORMAL);
  currentShoulder = SAFE_SHOULDER;
  currentElbow = SAFE_ELBOW;
  delayWithServer(600);  // زيادة وقت الرفع

  // تأكد من الارتفاع قبل الدوران
  Serial.printf("   ✅ Arm lifted to: Shoulder=%d°, Elbow=%d°\n", currentShoulder, currentElbow);

  int binBase = (bin == 0) ? BIN_REJECT_BASE : BIN_ACCEPT_BASE;
  Serial.printf("📦 [6/9] Moving to bin %d (base=%d°)...\n", bin, binBase);
  moveServoSmooth(baseServo, currentBase, binBase, SMOOTH_DELAY_NORMAL);
  currentBase = binBase;
  delayWithServer(700);

  Serial.println("⬇️ [7/9] Lowering into bin...");
  if (bin == 0) {
    // صندوق الرفض: أقصى اليمين + أقصى الشمال (أعلى)
    moveServoSmooth(shoulderServo, currentShoulder, BIN_REJECT_SHOULDER, SMOOTH_DELAY_NORMAL);
    moveServoSmooth(elbowServo, currentElbow, BIN_REJECT_ELBOW, SMOOTH_DELAY_NORMAL);
    currentShoulder = BIN_REJECT_SHOULDER;
    currentElbow = BIN_REJECT_ELBOW;
  } else {
    // صندوق القبول: ارتفاع معتدل
    moveServoSmooth(shoulderServo, currentShoulder, BIN_ACCEPT_SHOULDER, SMOOTH_DELAY_NORMAL);
    moveServoSmooth(elbowServo, currentElbow, BIN_ACCEPT_ELBOW, SMOOTH_DELAY_NORMAL);
    currentShoulder = BIN_ACCEPT_SHOULDER;
    currentElbow = BIN_ACCEPT_ELBOW;
  }
  delayWithServer(500);

  Serial.println("🔓 [8/9] Releasing...");
  moveServoSmooth(gripperServo, currentGripper, GRIPPER_OPEN, SMOOTH_DELAY_FAST);
  currentGripper = GRIPPER_OPEN;
  delayWithServer(600);

  Serial.println("🏠 [9/9] Safe Home...");
  safeHomePosition();

  Serial.println("✅ SORT COMPLETE!\n");
  return true;
}

// ===== حركة سلسة (مع معالجة الخادم) =====
void moveServoSmooth(Servo &servo, int fromAngle, int toAngle, int stepDelay) {
  if (fromAngle == toAngle) return;
  int step = (fromAngle < toAngle) ? 1 : -1;
  int current = fromAngle;

  Serial.printf("   Servo: %d° → %d° (delay=%dms)\n", fromAngle, toAngle, stepDelay);

  while (current != toAngle) {
    current += step;
    servo.write(current);

    // ← معالجة الخادم أثناء كل خطوة!
    server.handleClient();

    delay(stepDelay);
  }
}