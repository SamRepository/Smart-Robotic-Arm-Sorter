
#!/usr/bin/env python3
"""
Robot Arm Control Server - Fixed Version
IP: 10.42.0.1:5000
Camera: Index 1 (V4L2)
"""

from flask import Flask, jsonify, render_template_string, Response, request
from flask_socketio import SocketIO, emit
import cv2
import numpy as np
import threading
import time
import requests
import json
from ultralytics import YOLO
import os

# ===== Flask & SocketIO =====
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ===== Network =====
ESP32_ARM_URL = "http://10.42.0.224"  # IP of ESP32

# ===== Load YOLO =====
print("Loading custom YOLO model...")
model = YOLO('best.pt', verbose=False)
print("✅ Custom YOLO model loaded")

CLASS_NAMES = {
    0: 'cap', 1: 'crumbled', 2: 'label',
    3: 'no-cap', 4: 'not-crumbled'
}

# ===== Sorting Filters =====
sorting_filters = {
    'remove_no_label': False,
    'remove_no_cap': False,
    'remove_crumbled': False,
    'remove_all_defects': False
}

# ===== Locks =====
frame_lock = threading.Lock()
detection_lock = threading.Lock()
stats_lock = threading.Lock()

# ===== System Variables =====
system_running = False
current_frame = None
bottles_detected = 0
bottles_sorted = 0
last_detection_time = 0
latest_detections = []
latest_decision = None
latest_decision_text = "في انتظار الكشف..."

# ===== Arm Variables =====
arm_busy = False
last_sort_time = 0

HTML_PAGE = '''
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>الذراع الروبوتي الذكي - فرز متعدد العيوب</title>
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .dashboard {
            max-width: 1400px;
            margin: 0 auto;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }
        .card {
            background: white;
            border-radius: 20px;
            padding: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        .full-width { grid-column: span 2; }
        .video-container {
            background: #000;
            border-radius: 15px;
            overflow: hidden;
        }
        #videoFeed { width: 100%; height: auto; display: block; }
        .controls {
            display: flex;
            gap: 15px;
            justify-content: center;
            margin: 20px 0;
            flex-wrap: wrap;
        }
        button {
            padding: 15px 40px;
            font-size: 1.2em;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.3s;
            font-weight: bold;
        }
        .btn-start { background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); color: white; }
        .btn-stop { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color: white; }
        .btn-reset { background: linear-gradient(135deg, #ffa17f 0%, #00223e 100%); color: white; }
        button:hover { transform: translateY(-2px); box-shadow: 0 5px 15px rgba(0,0,0,0.2); }
        .filter-options {
            background: #f8f9fa;
            border-radius: 15px;
            padding: 15px;
            margin: 15px 0;
        }
        .filter-title {
            font-weight: bold;
            margin-bottom: 10px;
            color: #333;
        }
        .filter-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
        }
        .filter-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 12px;
            background: white;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
            border-right: 3px solid #ddd;
        }
        .filter-item:hover {
            background: #e9ecef;
            transform: translateX(-3px);
        }
        .filter-item input {
            width: 18px;
            height: 18px;
            cursor: pointer;
        }
        .filter-item label {
            flex: 1;
            cursor: pointer;
            font-size: 0.9em;
        }
        .filter-item.danger { border-right-color: #dc3545; }
        .filter-item.warning { border-right-color: #ffc107; }
        .filter-item.dark { border-right-color: #343a40; }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 15px;
            margin-top: 15px;
        }
        .stat-card {
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            padding: 20px;
            border-radius: 10px;
            text-align: center;
        }
        .stat-number {
            font-size: 2.5em;
            font-weight: bold;
            color: #667eea;
        }
        .status-indicator {
            display: inline-block;
            width: 15px;
            height: 15px;
            border-radius: 50%;
            margin-left: 10px;
        }
        .status-idle { background: #999; }
        .status-detecting { background: #4caf50; animation: pulse 1.5s infinite; }
        .status-moving { background: #ff9800; animation: blink 0.5s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
        .decision-box {
            background: #1e1e1e;
            border-radius: 10px;
            padding: 15px;
            margin-top: 15px;
            text-align: center;
        }
        .decision-accept {
            color: #4caf50;
            font-size: 1.2em;
            font-weight: bold;
        }
        .decision-reject {
            color: #f5576c;
            font-size: 1.2em;
            font-weight: bold;
        }
        .log-area {
            background: #1e1e1e;
            border-radius: 10px;
            padding: 15px;
            height: 250px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 0.85em;
            color: #0f0;
        }
        .log-entry { padding: 5px; border-bottom: 1px solid #333; }
        .log-entry.info { color: #4ecdc4; }
        .log-entry.success { color: #4caf50; }
        .log-entry.error { color: #f5576c; }
        .log-entry.warning { color: #ffc107; }
        @media (max-width: 768px) {
            .dashboard { grid-template-columns: 1fr; }
            .full-width { grid-column: span 1; }
            .filter-grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="dashboard">
        <div class="card full-width">
            <h1>🤖 الذراع الروبوتي الذكي - فرز القوارير</h1>
            <p style="text-align: center; color: #666;">نظام ذكي باستخدام YOLO للكشف عن العيوب (غطاء - ملصق - تلف)</p>
            <div class="controls">
                <button class="btn-start" onclick="startSystem()">▶ بدء النظام</button>
                <button class="btn-stop" onclick="stopSystem()">⏹ إيقاف النظام</button>
                <button class="btn-reset" onclick="resetStats()">🔄 إعادة تعيين الإحصائيات</button>
            </div>
        </div>
        <div class="card video-container">
            <h3>📹 البث المباشر - كشف العيوب</h3>
            <img id="videoFeed" src="/video_feed" alt="Video Feed" onerror="this.src='/video_feed'">
        </div>
        <div class="card">
            <h3>⚙️ إعدادات الفرز</h3>
            <div class="filter-options">
                <div class="filter-title">🎯 اختر معايير الرفض:</div>
                <div class="filter-grid">
                    <div class="filter-item danger" onclick="toggleCheckbox('remove_no_label')">
                        <input type="checkbox" id="remove_no_label" onchange="updateFilters()">
                        <label>❌ رفض: بدون ملصق (no label)</label>
                    </div>
                    <div class="filter-item danger" onclick="toggleCheckbox('remove_no_cap')">
                        <input type="checkbox" id="remove_no_cap" onchange="updateFilters()">
                        <label>❌ رفض: بدون غطاء (no-cap)</label>
                    </div>
                    <div class="filter-item warning" onclick="toggleCheckbox('remove_crumbled')">
                        <input type="checkbox" id="remove_crumbled" onchange="updateFilters()">
                        <label>⚠️ رفض: مجعد (crumbled)</label>
                    </div>
                    <div class="filter-item dark" onclick="toggleCheckbox('remove_all_defects')">
                        <input type="checkbox" id="remove_all_defects" onchange="updateFilters()">
                        <label>🚫 رفض: جميع العيوب</label>
                    </div>
                </div>
            </div>
            <h3>📊 الإحصائيات</h3>
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-number" id="bottlesDetected">0</div>
                    <div>قوارير مكتشفة</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number" id="bottlesSorted">0</div>
                    <div>قوارير مفروزة</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number" id="successRate">0%</div>
                    <div>نسبة القبول</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number" id="currentState">
                        <span class="status-indicator status-idle"></span>
                        متوقف
                    </div>
                    <div>حالة النظام</div>
                </div>
            </div>
            <div class="decision-box" id="decisionBox">
                <div style="color: #888;">في انتظار الكشف...</div>
            </div>
        </div>
        <div class="card full-width">
            <h3>📝 سجل العمليات</h3>
            <div class="log-area" id="logArea">
                <div class="log-entry info">✅ النظام جاهز - انتظر بدء التشغيل</div>
                <div class="log-entry info">🤖 تم تحميل نموذج YOLO المخصص للكشف عن العيوب</div>
                <div class="log-entry info">📋 الكلاسات المتاحة: cap, label, no-cap, crumbled, not-crumbled</div>
            </div>
        </div>
    </div>
    <script>
        const socket = io({reconnectionDelay: 500, reconnectionDelayMax: 2000});
        socket.on('connect', function() {
            addLog('🟢 متصل بخادم Raspberry Pi', 'info');
        });
        socket.on('disconnect', function() {
            addLog('🔴 انقطع الاتصال بالخادم', 'error');
        });
        socket.on('system_state', function(data) {
            if (data.state === 'running') {
                document.getElementById("currentState").innerHTML = '<span class="status-indicator status-detecting"></span> يعمل';
                addLog('🚀 تم بدء تشغيل النظام - بدء الكشف عن العيوب', 'info');
            } else {
                document.getElementById("currentState").innerHTML = '<span class="status-indicator status-idle"></span> متوقف';
                addLog('🛑 تم إيقاف النظام', 'info');
            }
        });
        socket.on('detection_result', function(data) {
            document.getElementById("bottlesDetected").innerHTML = data.detected;
            updateSuccessRate();
            const box = document.getElementById("decisionBox");
            if (data.decision === 'accept') {
                box.innerHTML = '<div class="decision-accept">✅ قرار: قبول وفرز</div>' +
                                '<div style="color: #fff; margin-top: 5px;">' + data.reason + '</div>';
                addLog('✅ ' + data.detected_class + ' - مقبول - ' + data.reason, 'success');
            } else {
                box.innerHTML = '<div class="decision-reject">❌ قرار: رفض</div>' +
                                '<div style="color: #fff; margin-top: 5px;">' + data.reason + '</div>';
                addLog('❌ مرفوض - ' + data.reason + ' (' + data.detected_class + ')', 'error');
            }
        });
        socket.on('sort_complete', function(data) {
            document.getElementById("bottlesSorted").innerHTML = data.sorted;
            updateSuccessRate();
            addLog('✅ تم فرز القارورة إلى الصندوق رقم ' + data.bin + ' في ' + data.time + ' ثانية', 'success');
        });
        function toggleCheckbox(id) {
            const cb = document.getElementById(id);
            if (event.target !== cb) {
                cb.checked = !cb.checked;
                updateFilters();
            }
        }
        function updateFilters() {
            const filters = {
                remove_no_label: document.getElementById("remove_no_label").checked,
                remove_no_cap: document.getElementById("remove_no_cap").checked,
                remove_crumbled: document.getElementById("remove_crumbled").checked,
                remove_all_defects: document.getElementById("remove_all_defects").checked
            };
            if (filters.remove_all_defects) {
                document.getElementById("remove_no_label").checked = false;
                document.getElementById("remove_no_cap").checked = false;
                document.getElementById("remove_crumbled").checked = false;
                filters.remove_no_label = false;
                filters.remove_no_cap = false;
                filters.remove_crumbled = false;
            }
            fetch('/update_filters', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(filters)
            }).then(r => r.json()).then(data => {
                let msg = '';
                if (filters.remove_all_defects) msg = 'رفض جميع العيوب';
                else {
                    let active = [];
                    if (filters.remove_no_label) active.push('بدون ملصق');
                    if (filters.remove_no_cap) active.push('بدون غطاء');
                    if (filters.remove_crumbled) active.push('مجعد');
                    msg = active.length ? 'رفض: ' + active.join(', ') : 'قبول الكل';
                }
                addLog('⚙️ تغيير إعدادات الفرز: ' + msg, 'info');
            }).catch(e => {
                addLog('❌ خطأ في تحديث الإعدادات', 'error');
            });
        }
        function startSystem() {
            fetch('/start', {method: 'POST'})
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'success') {
                        addLog('📸 بدء التصوير وتشغيل كشف العيوب بـ YOLO', 'info');
                    }
                }).catch(e => addLog('❌ خطأ في بدء النظام', 'error'));
        }
        function stopSystem() {
            fetch('/stop', {method: 'POST'})
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'success') {
                        addLog('🛑 تم إيقاف الكشف والفرز', 'info');
                    }
                }).catch(e => addLog('❌ خطأ في إيقاف النظام', 'error'));
        }
        function resetStats() {
            fetch('/reset_stats', {method: 'POST'})
                .then(() => {
                    document.getElementById("bottlesDetected").innerHTML = '0';
                    document.getElementById("bottlesSorted").innerHTML = '0';
                    updateSuccessRate();
                    addLog('🔄 تم إعادة تعيين الإحصائيات', 'info');
                }).catch(e => addLog('❌ خطأ في إعادة التعيين', 'error'));
        }
        function updateSuccessRate() {
            const detected = parseInt(document.getElementById("bottlesDetected").innerHTML);
            const sorted = parseInt(document.getElementById("bottlesSorted").innerHTML);
            const rate = detected > 0 ? ((sorted / detected) * 100).toFixed(1) : 0;
            document.getElementById("successRate").innerHTML = rate + '%';
        }
        function addLog(message, type = 'info') {
            const logArea = document.getElementById("logArea");
            const logEntry = document.createElement("div");
            logEntry.className = 'log-entry ' + type;
            logEntry.innerHTML = '[' + new Date().toLocaleTimeString() + '] ' + message;
            logArea.insertBefore(logEntry, logArea.firstChild);
            while (logArea.children.length > 100) {
                logArea.removeChild(logArea.lastChild);
            }
        }
        setInterval(() => {
            fetch('/status')
                .then(response => response.json())
                .then(data => {
                    document.getElementById("bottlesDetected").innerHTML = data.detected;
                    document.getElementById("bottlesSorted").innerHTML = data.sorted;
                    updateSuccessRate();
                }).catch(() => {});
        }, 2000);
    </script>
</body>
</html>
'''

# ===== دالة اتخاذ قرار الفرز =====
def make_decision(detections):
    if not detections:
        return {
            'decision': 'reject',
            'reason': 'لم يتم الكشف عن منتج',
            'sort_bin': 0
        }
    detected_classes = [d['class_name'] for d in detections]
    defects = []
    has_label = 'label' in detected_classes
    has_no_cap = 'no-cap' in detected_classes
    is_crumbled = 'crumbled' in detected_classes
    if not has_label:
        defects.append('بدون ملصق')
    if has_no_cap:
        defects.append('بدون غطاء')
    if is_crumbled:
        defects.append('مجعد')
    reject_reasons = []
    if sorting_filters['remove_all_defects']:
        if defects:
            reject_reasons = defects
    else:
        if sorting_filters['remove_no_label'] and not has_label:
            reject_reasons.append('بدون ملصق')
        if sorting_filters['remove_no_cap'] and has_no_cap:
            reject_reasons.append('بدون غطاء')
        if sorting_filters['remove_crumbled'] and is_crumbled:
            reject_reasons.append('مجعد')
    if reject_reasons:
        return {
            'decision': 'reject',
            'reason': 'مرفوض: ' + ', '.join(reject_reasons),
            'sort_bin': 0  # ← صندوق الرفض
        }
    else:
        return {
            'decision': 'accept',
            'reason': 'منتج سليم',
            'sort_bin': 1  # ← صندوق القبول
        }

# ===== كشف العيوب باستخدام YOLO =====
def detect_defects(frame):
    try:
        results = model(frame, conf=0.4, verbose=False, max_det=10)
        detections = []
        for r in results:
            if r.boxes is not None:
                for box in r.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    conf = float(box.conf[0])
                    class_id = int(box.cls[0])
                    class_name = CLASS_NAMES.get(class_id, 'unknown_' + str(class_id))
                    if conf > 0.5:
                        detections.append({
                            "class_name": class_name,
                            "class_id": class_id,
                            "confidence": conf,
                            "bbox": [x1, y1, x2, y2],
                            "center_x": (x1 + x2) // 2,
                            "center_y": (y1 + y2) // 2
                        })
        return detections
    except Exception as e:
        print("❌ خطأ في الكشف: " + str(e))
        return []

# ===== التقاط من الكاميرا (Thread منفصل) =====
def capture_camera():
    global current_frame
    cap = cv2.VideoCapture(1, cv2.CAP_V4L2)
    if not cap.isOpened():
        print("❌ لا يمكن فتح الكاميرا على index 1 بـ V4L2")
        cap = cv2.VideoCapture(1)
        if not cap.isOpened():
            print("❌ فشل فتح الكاميرا تماماً")
            return
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    print("✅ الكاميرا تعمل على index 1")
    while True:
        ret, frame = cap.read()
        if ret and frame is not None:
            with frame_lock:
                current_frame = frame
        else:
            print("⚠️ فشل قراءة إطار من الكاميرا")
            time.sleep(0.1)
    cap.release()

# ===== Thread الكشف المنفصل =====
def detection_worker():
    global bottles_detected, bottles_sorted, last_detection_time
    global latest_detections, latest_decision, latest_decision_text
    print("✅ Thread الكشف بدأ العمل")
    while True:
        try:
            if not system_running:
                time.sleep(0.2)
                continue
            if time.time() - last_detection_time < 1.5:
                time.sleep(0.1)
                continue
            with frame_lock:
                if current_frame is None:
                    time.sleep(0.1)
                    continue
                frame = current_frame.copy()
            detections = detect_defects(frame)
            with detection_lock:
                latest_detections = detections
            if len(detections) > 0:
                last_detection_time = time.time()
                frame_center_x = frame.shape[1] // 2
                closest = min(detections, key=lambda d: abs(d["center_x"] - frame_center_x))
                decision = make_decision([closest])
                with detection_lock:
                    latest_decision = decision
                    latest_decision_text = decision['reason']
                with stats_lock:
                    bottles_detected += 1
                socketio.emit('detection_result', {
                    'detected': bottles_detected,
                    'decision': decision['decision'],
                    'reason': decision['reason'],
                    'detected_class': closest['class_name']
                })
                # ===== إرسال أمر للذراع (قبول أو رفض) =====
                # bin=0: صندوق الرفض (BIN_REJECT_BASE = 30°)
                # bin=1: صندوق القبول (BIN_ACCEPT_BASE = 150°)
                try:
                    with stats_lock:
                        bottles_sorted += 1
                    socketio.emit('sort_complete', {
                        'bin': decision['sort_bin'],
                        'sorted': bottles_sorted,
                        'time': 0.5
                    })
                    if not arm_busy:
                        print(f"🦾 قرار: {decision['decision']} → صندوق {decision['sort_bin']}")
                        arm_thread = threading.Thread(
                            target=sort_bottle,
                            args=(decision['sort_bin'], closest["center_x"], closest["center_y"]),
                            daemon=True
                        )
                        arm_thread.start()
                    else:
                        print("⚠️ الذراع مشغول - تم تخطي الفرز")
                except Exception as e:
                    print("❌ خطأ في الفرز: " + str(e))
            time.sleep(0.1)
        except Exception as e:
            print("❌ خطأ في thread الكشف: " + str(e))
            time.sleep(0.5)

# ===== توليد إطارات الفيديو للبث =====
def generate_frames():
    while True:
        try:
            with frame_lock:
                frame = current_frame.copy() if current_frame is not None else None
            if frame is None:
                time.sleep(0.05)
                continue
            with detection_lock:
                detections = latest_detections.copy() if latest_detections else []
                decision_text = latest_decision_text if latest_decision_text else "في انتظار الكشف..."
                decision_type = latest_decision['decision'] if latest_decision else 'wait'
            colors = {
                'cap': (0, 255, 0),
                'label': (255, 255, 0),
                'no-cap': (0, 0, 255),
                'crumbled': (255, 0, 0),
                'not-crumbled': (0, 255, 255)
            }
            for det in detections:
                x1, y1, x2, y2 = det["bbox"]
                class_name = det["class_name"]
                conf = det["confidence"]
                color = colors.get(class_name, (255, 255, 255))
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, class_name + " " + str(round(conf, 2)),
                            (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                            color, 2)
            with stats_lock:
                detected_count = bottles_detected
                sorted_count = bottles_sorted
            status_text = "RUNNING" if system_running else "STOPPED"
            status_color = (0, 255, 0) if system_running else (0, 0, 255)
            cv2.putText(frame, "Status: " + status_text,
                        (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        status_color, 2)
            cv2.putText(frame, "Detected: " + str(detected_count),
                        (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (255, 255, 255), 2)
            cv2.putText(frame, "Sorted: " + str(sorted_count),
                        (10, 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (255, 255, 255), 2)
            decision_color = (0, 255, 0) if decision_type == 'accept' else (0, 0, 255) if decision_type == 'reject' else (128, 128, 128)
            cv2.putText(frame, "Decision: " + decision_text,
                        (10, 120),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        decision_color, 2)
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if not ret:
                continue
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            time.sleep(0.02)
        except Exception as e:
            print("❌ خطأ في توليد الإطار: " + str(e))
            time.sleep(0.1)

# ===== إرسال أمر الفرز إلى ESP32 =====
def sort_bottle(bin_number, x, y):
    global arm_busy, last_sort_time
    if arm_busy:
        print("⚠️ الذراع مشغول - تم تجاهل الطلب")
        return False
    if time.time() - last_sort_time < 5:
        print("⚠️ فاصل زمني قصير - تم تجاهل الطلب")
        return False
    arm_busy = True
    last_sort_time = time.time()
    try:
        print(f"🦾 إرسال أمر الفرز: bin={bin_number}, x={x}, y={y}")
        response = requests.get(
            ESP32_ARM_URL + "/sort?bin=" + str(bin_number) + "&x=" + str(x) + "&y=" + str(y),
            timeout=30
        )
        if response.status_code == 200:
            print("✅ تم إرسال أمر الفرز بنجاح")
            return True
        elif response.status_code == 409:
            print("⚠️ الذراع مشغول (409)")
            return False
        else:
            print(f"⚠️ رد غير متوقع: {response.status_code}")
            return False
    except requests.exceptions.Timeout:
        print("⚠️ انتهى وقت الانتظار (30 ثانية) - الذراع قد يكون يعمل")
        return False
    except Exception as e:
        print("⚠️ خطأ في الاتصال بالذراع: " + str(e))
        return False
    finally:
        def reset_arm():
            global arm_busy
            time.sleep(20)
            arm_busy = False
            print("✅ الذراع جاهز للفرز التالي")
        threading.Thread(target=reset_arm, daemon=True).start()

# ===== Routes =====
@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(),
                   mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/start', methods=['POST'])
def start():
    global system_running, last_detection_time
    system_running = True
    last_detection_time = 0
    socketio.emit('system_state', {'state': 'running'})
    return jsonify({"status": "success", "message": "تم بدء النظام"})

@app.route('/stop', methods=['POST'])
def stop():
    global system_running
    system_running = False
    socketio.emit('system_state', {'state': 'stopped'})
    return jsonify({"status": "success", "message": "تم إيقاف النظام"})

@app.route('/reset_stats', methods=['POST'])
def reset_stats():
    global bottles_detected, bottles_sorted, last_detection_time
    global latest_detections, latest_decision, latest_decision_text
    with stats_lock:
        bottles_detected = 0
        bottles_sorted = 0
    last_detection_time = 0
    with detection_lock:
        latest_detections = []
        latest_decision = None
        latest_decision_text = "في انتظار الكشف..."
    return jsonify({"status": "success"})

@app.route('/update_filters', methods=['POST'])
def update_filters():
    global sorting_filters
    data = request.get_json()
    sorting_filters.update(data)
    print("⚙️ Filters updated: " + str(sorting_filters))
    return jsonify({"status": "success", "filters": sorting_filters})

@app.route('/status', methods=['GET'])
def status():
    with stats_lock:
        return jsonify({"detected": bottles_detected, "sorted": bottles_sorted})

@app.route('/arm_busy', methods=['GET'])
def arm_busy_status():
    return jsonify({"arm_busy": arm_busy})

if __name__ == '__main__':
    print("\n" + "="*50)
    print("🤖 Robot Arm Control Server - Fixed Version")
    print("="*50)
    print("📡 IP: 10.42.0.1:5000")
    print("📸 Camera: Index 1 (V4L2)")
    print("🦾 ESP32 Arm: 10.42.0.224")
    print("📋 Classes: cap, label, no-cap, crumbled, not-crumbled")
    print("="*50)
    camera_thread = threading.Thread(target=capture_camera, daemon=True)
    camera_thread.start()
    detect_thread = threading.Thread(target=detection_worker, daemon=True)
    detect_thread.start()
    print("✅ جميع Threads تعمل")
    print("🌐 افتح المتصفح على: http://10.42.0.1:5000")
    print("="*50 + "\n")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
