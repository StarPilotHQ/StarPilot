import { reactive } from "vue"

const STORAGE_KEY = "galaxy-language"

export const LANGUAGE_OPTIONS = [
  { value: "en", label: "English" },
  { value: "es", label: "Spanish" },
  { value: "fr", label: "French" },
  { value: "ko", label: "Korean" },
  { value: "zh-CHS", label: "Chinese" },
  { value: "vi", label: "Vietnamese" },
]

const SUPPORTED_CODES = new Set(LANGUAGE_OPTIONS.map((option) => option.value))

// Galaxy deliberately keeps English as the fallback. This lets new server-side
// labels ship safely before they have been added to every translation below.
const TRANSLATIONS = {
  es: {
    English: "Inglés", Spanish: "Español", French: "Francés", Korean: "Coreano", Chinese: "Chino", Vietnamese: "Vietnamita",
    Home: "Inicio", Toggles: "Interruptores", Tools: "Herramientas", Recordings: "Grabaciones",
    Bluetooth: "Bluetooth", "Cameras & Monitoring": "Cámaras y monitoreo", Galaxy: "Galaxy",
    "Logs & Diagnostics": "Registros y diagnósticos", "Model Manager": "Administrador de modelos",
    "Navigation & Maps": "Navegación y mapas", "System Tools": "Herramientas del sistema",
    "Model Laboratory": "Laboratorio de modelos", Plots: "Gráficas", "Testing Ground": "Área de pruebas",
    "Theme Maker": "Creador de temas", "Tuning, Plots & Testing": "Ajustes, gráficas y pruebas",
    "Vehicle Controls": "Controles del vehículo", Main: "Principal", Offline: "Sin conexión", Parked: "Estacionado",
    Back: "Atrás", Menu: "Menú", "Galaxy home": "Inicio de Galaxy", "Search toggles...": "Buscar interruptores...",
    "Search toggles": "Buscar interruptores", "Clear search": "Borrar búsqueda", "Dark mode": "Modo oscuro",
    "Light mode": "Modo claro", "Switch to dark mode": "Cambiar a modo oscuro", "Switch to light mode": "Cambiar a modo claro",
    Settings: "Configuración", Language: "Idioma", "Select language": "Seleccionar idioma", Advanced: "Avanzado",
    "result(s)": "resultado(s)",
    "Galaxy uses English when no language is selected.": "Galaxy usa inglés si no se selecciona un idioma.",
    "Language updated.": "Idioma actualizado.", "Unable to save language.": "No se pudo guardar el idioma.",
    "Loading configuration...": "Cargando configuración...", "No settings available.": "No hay ajustes disponibles.",
    "No settings in this section.": "No hay ajustes en esta sección.", "Locked:": "Bloqueado:", "Step:": "Paso:",
    "This setting can only be changed while parked.": "Este ajuste solo se puede cambiar mientras el vehículo está estacionado.",
    Default: "Predeterminado", "Loading...": "Cargando...", "No options available": "No hay opciones disponibles",
    "Working...": "Procesando...", Run: "Ejecutar", Manage: "Administrar", Close: "Cerrar", Stock: "Original",
  },
  fr: {
    English: "Anglais", Spanish: "Espagnol", French: "Français", Korean: "Coréen", Chinese: "Chinois", Vietnamese: "Vietnamien",
    Home: "Accueil", Toggles: "Options", Tools: "Outils", Recordings: "Enregistrements",
    Bluetooth: "Bluetooth", "Cameras & Monitoring": "Caméras et surveillance", Galaxy: "Galaxy",
    "Logs & Diagnostics": "Journaux et diagnostics", "Model Manager": "Gestionnaire de modèles",
    "Navigation & Maps": "Navigation et cartes", "System Tools": "Outils système",
    "Model Laboratory": "Laboratoire de modèles", Plots: "Graphiques", "Testing Ground": "Zone de test",
    "Theme Maker": "Créateur de thèmes", "Tuning, Plots & Testing": "Réglages, graphiques et tests",
    "Vehicle Controls": "Commandes du véhicule", Main: "Principal", Offline: "Hors ligne", Parked: "Stationné",
    Back: "Retour", Menu: "Menu", "Galaxy home": "Accueil Galaxy", "Search toggles...": "Rechercher des options...",
    "Search toggles": "Rechercher des options", "Clear search": "Effacer la recherche", "Dark mode": "Mode sombre",
    "Light mode": "Mode clair", "Switch to dark mode": "Passer au mode sombre", "Switch to light mode": "Passer au mode clair",
    Settings: "Paramètres", Language: "Langue", "Select language": "Choisir la langue", Advanced: "Avancé",
    "result(s)": "résultat(s)",
    "Galaxy uses English when no language is selected.": "Galaxy utilise l’anglais si aucune langue n’est sélectionnée.",
    "Language updated.": "Langue mise à jour.", "Unable to save language.": "Impossible d’enregistrer la langue.",
    "Loading configuration...": "Chargement de la configuration...", "No settings available.": "Aucun réglage disponible.",
    "No settings in this section.": "Aucun réglage dans cette section.", "Locked:": "Verrouillé :", "Step:": "Pas :",
    "This setting can only be changed while parked.": "Ce réglage ne peut être modifié que lorsque le véhicule est stationné.",
    Default: "Par défaut", "Loading...": "Chargement...", "No options available": "Aucune option disponible",
    "Working...": "En cours...", Run: "Exécuter", Manage: "Gérer", Close: "Fermer", Stock: "Origine",
  },
  ko: {
    English: "영어", Spanish: "스페인어", French: "프랑스어", Korean: "한국어", Chinese: "중국어", Vietnamese: "베트남어",
    Home: "홈", Toggles: "토글", Tools: "도구", Recordings: "녹화",
    Bluetooth: "블루투스", "Cameras & Monitoring": "카메라 및 모니터링", Galaxy: "Galaxy",
    "Logs & Diagnostics": "로그 및 진단", "Model Manager": "모델 관리자",
    "Navigation & Maps": "내비게이션 및 지도", "System Tools": "시스템 도구",
    "Model Laboratory": "모델 연구소", Plots: "플롯", "Testing Ground": "테스트 공간",
    "Theme Maker": "테마 만들기", "Tuning, Plots & Testing": "튜닝, 플롯 및 테스트",
    "Vehicle Controls": "차량 제어", Main: "메인", Offline: "오프라인", Parked: "주차됨",
    Back: "뒤로", Menu: "메뉴", "Galaxy home": "Galaxy 홈", "Search toggles...": "토글 검색...",
    "Search toggles": "토글 검색", "Clear search": "검색 지우기", "Dark mode": "다크 모드",
    "Light mode": "라이트 모드", "Switch to dark mode": "다크 모드로 전환", "Switch to light mode": "라이트 모드로 전환",
    Settings: "설정", Language: "언어", "Select language": "언어 선택", Advanced: "고급",
    "result(s)": "개 결과",
    "Galaxy uses English when no language is selected.": "언어를 선택하지 않으면 Galaxy는 영어를 사용합니다.",
    "Language updated.": "언어가 업데이트되었습니다.", "Unable to save language.": "언어를 저장할 수 없습니다.",
    "Loading configuration...": "설정을 불러오는 중...", "No settings available.": "사용 가능한 설정이 없습니다.",
    "No settings in this section.": "이 섹션에 설정이 없습니다.", "Locked:": "잠김:", "Step:": "단계:",
    "This setting can only be changed while parked.": "이 설정은 주차 중에만 변경할 수 있습니다.",
    Default: "기본값", "Loading...": "로드 중...", "No options available": "사용 가능한 옵션이 없습니다",
    "Working...": "처리 중...", Run: "실행", Manage: "관리", Close: "닫기", Stock: "기본",
  },
  "zh-CHS": {
    English: "英语", Spanish: "西班牙语", French: "法语", Korean: "韩语", Chinese: "中文", Vietnamese: "越南语",
    Home: "主页", Toggles: "开关", Tools: "工具", Recordings: "录制内容",
    Bluetooth: "蓝牙", "Cameras & Monitoring": "摄像头和监控", Galaxy: "Galaxy",
    "Logs & Diagnostics": "日志和诊断", "Model Manager": "模型管理器",
    "Navigation & Maps": "导航和地图", "System Tools": "系统工具",
    "Model Laboratory": "模型实验室", Plots: "图表", "Testing Ground": "测试区",
    "Theme Maker": "主题制作器", "Tuning, Plots & Testing": "调校、图表和测试",
    "Vehicle Controls": "车辆控制", Main: "主菜单", Offline: "离线", Parked: "已停车",
    Back: "返回", Menu: "菜单", "Galaxy home": "Galaxy 主页", "Search toggles...": "搜索开关...",
    "Search toggles": "搜索开关", "Clear search": "清除搜索", "Dark mode": "深色模式",
    "Light mode": "浅色模式", "Switch to dark mode": "切换到深色模式", "Switch to light mode": "切换到浅色模式",
    Settings: "设置", Language: "语言", "Select language": "选择语言", Advanced: "高级",
    "result(s)": "个结果",
    "Galaxy uses English when no language is selected.": "未选择语言时，Galaxy 将使用英语。",
    "Language updated.": "语言已更新。", "Unable to save language.": "无法保存语言。",
    "Loading configuration...": "正在加载配置...", "No settings available.": "没有可用设置。",
    "No settings in this section.": "此部分没有设置。", "Locked:": "已锁定：", "Step:": "步长：",
    "This setting can only be changed while parked.": "此设置只能在车辆停放时更改。",
    Default: "默认值", "Loading...": "加载中...", "No options available": "没有可用选项",
    "Working...": "处理中...", Run: "运行", Manage: "管理", Close: "关闭", Stock: "原厂",
  },
  vi: {
    English: "Tiếng Anh", Spanish: "Tiếng Tây Ban Nha", French: "Tiếng Pháp", Korean: "Tiếng Hàn", Chinese: "Tiếng Trung", Vietnamese: "Tiếng Việt",
    Home: "Trang chủ", Toggles: "Tùy chọn", Tools: "Công cụ", Recordings: "Bản ghi",
    Bluetooth: "Bluetooth", "Cameras & Monitoring": "Camera & giám sát", Galaxy: "Galaxy",
    "Logs & Diagnostics": "Nhật ký & chẩn đoán", "Model Manager": "Quản lý mô hình",
    "Navigation & Maps": "Điều hướng & bản đồ", "System Tools": "Công cụ hệ thống",
    "Model Laboratory": "Phòng thử nghiệm mô hình", Plots: "Biểu đồ", "Testing Ground": "Khu vực thử nghiệm",
    "Theme Maker": "Tạo giao diện", "Tuning, Plots & Testing": "Tinh chỉnh, biểu đồ & thử nghiệm",
    "Vehicle Controls": "Điều khiển xe", Main: "Chính", Offline: "Ngoại tuyến", Parked: "Đang đỗ",
    Back: "Quay lại", Menu: "Menu", "Galaxy home": "Trang chủ Galaxy", "Search toggles...": "Tìm tùy chọn...",
    "Search toggles": "Tìm tùy chọn", "Clear search": "Xóa tìm kiếm", "Dark mode": "Chế độ tối",
    "Light mode": "Chế độ sáng", "Switch to dark mode": "Chuyển sang chế độ tối", "Switch to light mode": "Chuyển sang chế độ sáng",
    Settings: "Cài đặt", Language: "Ngôn ngữ", "Select language": "Chọn ngôn ngữ", Advanced: "Nâng cao",
    "result(s)": "kết quả",
    "Galaxy uses English when no language is selected.": "Galaxy sẽ dùng tiếng Anh nếu chưa chọn ngôn ngữ.",
    "Language updated.": "Đã cập nhật ngôn ngữ.", "Unable to save language.": "Không thể lưu ngôn ngữ.",
    "Loading configuration...": "Đang tải cấu hình...", "No settings available.": "Không có cài đặt nào.",
    "No settings in this section.": "Không có cài đặt nào trong mục này.", "Locked:": "Đã khóa:", "Step:": "Bước:",
    "This setting can only be changed while parked.": "Chỉ có thể thay đổi cài đặt này khi xe đang đỗ.",
    Default: "Mặc định", "Loading...": "Đang tải...", "No options available": "Không có tùy chọn nào",
    "Working...": "Đang xử lý...", Run: "Chạy", Manage: "Quản lý", Close: "Đóng", Stock: "Nguyên bản",
  },
}

// The settings catalog is shared with the native UI, so most of its labels
// arrive from the device rather than this bundle. These common section names
// and controls keep the Galaxy settings screen translated as well.
const MORE_TRANSLATIONS = {
  es: {
    Favorites: "Favoritos", "Lateral (Steering)": "Lateral (Dirección)",
    "Longitudinal (Speed & Following)": "Longitudinal (Velocidad y seguimiento)",
    "Vision Speed Limits": "Límites de velocidad por visión", "Visual (Display & UI)": "Visual (Pantalla e interfaz)",
    "Sounds & Alerts": "Sonidos y alertas", Vehicle: "Vehículo", "Wheel Controls": "Controles del volante",
    "Device & Data": "Dispositivo y datos", Developer: "Desarrollador", "Advanced Lateral Tuning": "Ajuste lateral avanzado",
    "Advanced steering control changes to fine-tune how openpilot drives.": "Cambios avanzados en el control de la dirección para ajustar cómo conduce openpilot.",
    "Always On Lateral": "Lateral siempre activo", "openpilot's steering remains active even when the accelerator or brake pedals are pressed.": "La dirección de openpilot permanece activa incluso cuando se pisan el acelerador o los frenos.",
    "Lane Changes": "Cambios de carril", "Allow openpilot to change lanes.": "Permitir que openpilot cambie de carril.",
    "Lateral Tuning": "Ajuste lateral", "Miscellaneous steering control changes to fine-tune how openpilot drives.": "Cambios diversos del control de la dirección para ajustar cómo conduce openpilot.",
    "Quality of Life": "Calidad de vida", "Steering control changes to fine-tune how openpilot drives.": "Cambios del control de la dirección para ajustar cómo conduce openpilot.",
    "Enable V-ASM": "Activar V-ASM", "Favorites": "Favoritos", "Device & Data": "Dispositivo y datos",
    Routes: "Rutas", Route: "Ruta", Selected: "Seleccionada", Recommended: "Recomendada", Alternative: "Alternativa", Select: "Seleccionar", "Choose a route": "Elegir una ruta",
  },
  fr: {
    Favorites: "Favoris", "Lateral (Steering)": "Latéral (Direction)",
    "Longitudinal (Speed & Following)": "Longitudinal (Vitesse et suivi)",
    "Vision Speed Limits": "Limites de vitesse par vision", "Visual (Display & UI)": "Visuel (Affichage et interface)",
    "Sounds & Alerts": "Sons et alertes", Vehicle: "Véhicule", "Wheel Controls": "Commandes au volant",
    "Device & Data": "Appareil et données", Developer: "Développeur", "Advanced Lateral Tuning": "Réglage latéral avancé",
    "Advanced steering control changes to fine-tune how openpilot drives.": "Modifications avancées de la direction pour régler finement le comportement d’openpilot.",
    "Always On Lateral": "Direction latérale toujours active", "openpilot's steering remains active even when the accelerator or brake pedals are pressed.": "La direction d’openpilot reste active même lorsque l’accélérateur ou les freins sont enfoncés.",
    "Lane Changes": "Changements de voie", "Allow openpilot to change lanes.": "Autoriser openpilot à changer de voie.",
    "Lateral Tuning": "Réglage latéral", "Miscellaneous steering control changes to fine-tune how openpilot drives.": "Divers réglages de direction pour ajuster finement le comportement d’openpilot.",
    "Quality of Life": "Confort d’utilisation", "Steering control changes to fine-tune how openpilot drives.": "Réglages de direction pour ajuster finement le comportement d’openpilot.",
    "Enable V-ASM": "Activer V-ASM",
    Routes: "Itinéraires", Route: "Itinéraire", Selected: "Sélectionné", Recommended: "Recommandé", Alternative: "Alternative", Select: "Sélectionner", "Choose a route": "Choisir un itinéraire",
  },
  ko: {
    Favorites: "즐겨찾기", "Lateral (Steering)": "횡방향 (조향)",
    "Longitudinal (Speed & Following)": "종방향 (속도 및 추종)",
    "Vision Speed Limits": "비전 속도 제한", "Visual (Display & UI)": "시각 (디스플레이 및 UI)",
    "Sounds & Alerts": "소리 및 경고", Vehicle: "차량", "Wheel Controls": "휠 컨트롤",
    "Device & Data": "장치 및 데이터", Developer: "개발자", "Advanced Lateral Tuning": "고급 횡방향 튜닝",
    "Advanced steering control changes to fine-tune how openpilot drives.": "openpilot의 주행 방식을 세밀하게 조정하는 고급 조향 제어 변경입니다.",
    "Always On Lateral": "항상 활성화된 횡방향 제어", "openpilot's steering remains active even when the accelerator or brake pedals are pressed.": "가속 페달이나 브레이크 페달을 밟아도 openpilot 조향이 계속 활성화됩니다.",
    "Lane Changes": "차선 변경", "Allow openpilot to change lanes.": "openpilot이 차선을 변경하도록 허용합니다.",
    "Lateral Tuning": "횡방향 튜닝", "Miscellaneous steering control changes to fine-tune how openpilot drives.": "openpilot의 주행을 세밀하게 조정하는 기타 조향 제어 변경입니다.",
    "Quality of Life": "편의 기능", "Steering control changes to fine-tune how openpilot drives.": "openpilot의 주행을 세밀하게 조정하는 조향 제어 변경입니다.",
    "Enable V-ASM": "V-ASM 활성화",
    Routes: "경로", Route: "경로", Selected: "선택됨", Recommended: "추천", Alternative: "대안", Select: "선택", "Choose a route": "경로 선택",
  },
  "zh-CHS": {
    Favorites: "收藏", "Lateral (Steering)": "横向（转向）",
    "Longitudinal (Speed & Following)": "纵向（速度和跟车）", "Vision Speed Limits": "视觉限速",
    "Visual (Display & UI)": "视觉（显示和界面）", "Sounds & Alerts": "声音和提醒", Vehicle: "车辆",
    "Wheel Controls": "方向盘控制", "Device & Data": "设备和数据", Developer: "开发者", "Advanced Lateral Tuning": "高级横向调校",
    "Advanced steering control changes to fine-tune how openpilot drives.": "用于精细调整 openpilot 驾驶方式的高级转向控制设置。",
    "Always On Lateral": "始终启用横向控制", "openpilot's steering remains active even when the accelerator or brake pedals are pressed.": "即使踩下加速或制动踏板，openpilot 转向仍保持启用。",
    "Lane Changes": "变道", "Allow openpilot to change lanes.": "允许 openpilot 变道。", "Lateral Tuning": "横向调校",
    "Miscellaneous steering control changes to fine-tune how openpilot drives.": "用于精细调整 openpilot 驾驶方式的其他转向控制设置。",
    "Quality of Life": "使用体验", "Steering control changes to fine-tune how openpilot drives.": "用于精细调整 openpilot 驾驶方式的转向控制设置。",
    "Enable V-ASM": "启用 V-ASM",
    Routes: "路线", Route: "路线", Selected: "已选择", Recommended: "推荐", Alternative: "备选", Select: "选择", "Choose a route": "选择路线",
  },
  vi: {
    Favorites: "Yêu thích", "Lateral (Steering)": "Điều khiển ngang (đánh lái)",
    "Longitudinal (Speed & Following)": "Điều khiển dọc (tốc độ & khoảng cách)",
    "Vision Speed Limits": "Nhận diện giới hạn tốc độ bằng camera", "Visual (Display & UI)": "Hiển thị & giao diện",
    "Sounds & Alerts": "Âm thanh & cảnh báo", Vehicle: "Xe", "Wheel Controls": "Nút điều khiển trên vô lăng",
    "Device & Data": "Thiết bị & dữ liệu", Developer: "Nhà phát triển", "Advanced Lateral Tuning": "Tinh chỉnh điều khiển ngang nâng cao",
    "Advanced steering control changes to fine-tune how openpilot drives.": "Các tùy chỉnh nâng cao về điều khiển lái giúp tinh chỉnh cách openpilot vận hành xe.",
    "Always On Lateral": "Điều khiển ngang luôn bật", "openpilot's steering remains active even when the accelerator or brake pedals are pressed.": "Điều khiển lái của openpilot vẫn hoạt động ngay cả khi đạp ga hoặc phanh.",
    "Lane Changes": "Chuyển làn", "Allow openpilot to change lanes.": "Cho phép openpilot chuyển làn.",
    "Lateral Tuning": "Tinh chỉnh điều khiển ngang", "Miscellaneous steering control changes to fine-tune how openpilot drives.": "Các tùy chỉnh khác về điều khiển lái giúp tinh chỉnh cách openpilot vận hành xe.",
    "Quality of Life": "Tiện ích sử dụng", "Steering control changes to fine-tune how openpilot drives.": "Các tùy chỉnh điều khiển lái giúp tinh chỉnh cách openpilot vận hành xe.",
    "Enable V-ASM": "Bật V-ASM",
    Routes: "Tuyến đường", Route: "Tuyến đường", Selected: "Đã chọn", Recommended: "Đề xuất", Alternative: "Tuyến khác", Select: "Chọn", "Choose a route": "Chọn tuyến đường",
    Dashboard: "Bảng điều khiển", Refresh: "Làm mới", "Loading dashboard...": "Đang tải bảng điều khiển...",
    "Failed to load dashboard": "Không thể tải bảng điều khiển", "Couldn't refresh dashboard.": "Không thể làm mới bảng điều khiển.",
    "device online": "thiết bị đang trực tuyến", "device offline": "thiết bị đang ngoại tuyến",
    "Last drive": "Chuyến gần nhất", "Your driving": "Hoạt động lái xe", "This week": "Tuần này",
    "Personal records": "Kỷ lục cá nhân", "Recent drives": "Các chuyến gần đây", "Most used models": "Mô hình được dùng nhiều nhất",
    Storage: "Dung lượng lưu trữ", "Your device": "Thiết bị của bạn", Vitals: "Thông tin thiết bị", Software: "Phần mềm",
    "All time": "Từ trước đến nay", "Past week": "Tuần qua", duration: "thời lượng", analyzing: "đang phân tích",
    speed: "tốc độ", "mph avg": "mph TB", "kph avg": "km/h TB",
    "Longest drive": "Chuyến dài nhất", "Most-engaged day": "Ngày có tỷ lệ dùng openpilot cao nhất", "Best week": "Tuần tốt nhất",
    "Highest streak": "Chuỗi ngày lái xe dài nhất", "Longest undistracted drive": "Chuyến không mất tập trung dài nhất", "Clean-drive streak": "Chuỗi chuyến lái xe tập trung",
    "No local drives found yet.": "Chưa có chuyến lái nào được lưu trên thiết bị.", "Stats excluded": "Không tính vào thống kê",
    "Analyzing stats": "Đang phân tích thống kê", Excluded: "Không tính", Pending: "Đang chờ",
    "Ignored from stats": "Đã bỏ khỏi thống kê", "Waiting for full route analysis": "Đang chờ phân tích toàn bộ hành trình",
    "Include drive stats": "Tính chuyến này vào thống kê", "Ignore drive stats": "Bỏ chuyến này khỏi thống kê",
    "No model usage recorded yet.": "Chưa ghi nhận mô hình nào được sử dụng.", "Model usage share": "Tỷ lệ sử dụng mô hình",
    "using this model": "sử dụng mô hình này", "Dashcam footage": "Video camera hành trình", "High-resolution footage": "Video độ phân giải cao",
    "Konik footage": "Video Konik", "Free space": "Dung lượng trống", "No wireless connectivity": "Không có kết nối không dây",
    "LAN IP": "IP LAN", Uptime: "Thời gian hoạt động", "CPU temp": "Nhiệt độ CPU", "GPU temp": "Nhiệt độ GPU",
    Branch: "Nhánh", Build: "Bản dựng", Commit: "Commit", "Version date": "Ngày phiên bản",
    "Fork maintainer": "Người duy trì bản fork", "Update available": "Có bản cập nhật",
    "Ignore this drive's statistics?": "Bỏ chuyến này khỏi thống kê?",
    "It will no longer affect local weekly totals, records, model usage, engagement, or attention streaks.": "Chuyến này sẽ không còn được tính vào tổng số liệu hằng tuần, kỷ lục, mức sử dụng mô hình, mức sử dụng openpilot hoặc chuỗi chuyến lái xe tập trung được lưu trên thiết bị.",
    Ignore: "Bỏ qua", Confirm: "Xác nhận", Cancel: "Hủy", "No drives yet": "Chưa có chuyến nào", "Unknown model": "Không rõ mô hình",
    "Unable to ignore drive statistics.": "Không thể bỏ chuyến này khỏi thống kê.",
    "Unable to include drive statistics.": "Không thể tính chuyến này vào thống kê.",
    Mon: "T2", Tue: "T3", Wed: "T4", Thu: "T5", Fri: "T6", Sat: "T7", Sun: "CN",
    "Vehicle Features": "Tính năng xe", "Lock/Unlock Doors": "Khóa/Mở khóa cửa",
    "Send lock or unlock commands remotely to your vehicle.": "Gửi lệnh khóa hoặc mở khóa cửa xe từ xa.",
    "Toyota Security Keys": "Khóa bảo mật Toyota",
    "Manage and apply security keys for secOC protected devices.": "Quản lý và áp dụng khóa bảo mật cho các thiết bị được bảo vệ bằng secOC.",
    "These features verify vehicle compatibility when launched.": "Các tính năng này sẽ kiểm tra khả năng tương thích với xe khi khởi chạy.",
    "Checking...": "Đang kiểm tra...", "Not supported": "Không hỗ trợ",
    "Could not check vehicle compatibility.": "Không thể kiểm tra khả năng tương thích với xe.",
    "Lock/Unlock Doors is not supported for your current vehicle.": "Tính năng khóa/mở khóa cửa không được hỗ trợ trên xe hiện tại.",
    "Toyota Security Keys is not supported for your current vehicle.": "Khóa bảo mật Toyota không được hỗ trợ trên xe hiện tại.",
    "Remotely lock or unlock your car doors using the buttons below.": "Dùng các nút bên dưới để khóa hoặc mở khóa cửa xe từ xa.",
    "Lock Doors": "Khóa cửa", "Unlock Doors": "Mở khóa cửa", "Doors locked!": "Đã khóa cửa!", "Doors unlocked!": "Đã mở khóa cửa!",
    "Failed to lock doors.": "Không thể khóa cửa.", "Failed to unlock doors.": "Không thể mở khóa cửa.",
    "Select Key": "Chọn khóa", "-- Select a saved key --": "-- Chọn khóa đã lưu --", "Key Name": "Tên khóa", "Key Value": "Giá trị khóa",
    "Enter key name...": "Nhập tên khóa...", "Enter key value...": "Nhập giá trị khóa...",
    "A key with this name already exists.": "Đã có khóa với tên này.", "Save key": "Lưu khóa", "Delete key": "Xóa khóa", "Apply Key": "Áp dụng khóa",
    "Loading keys...": "Đang tải danh sách khóa...", "Failed to load keys...": "Không thể tải danh sách khóa...",
    "Invalid input or duplicate name.": "Dữ liệu không hợp lệ hoặc tên đã tồn tại.", "Saved key!": "Đã lưu khóa!", "Save failed...": "Không thể lưu khóa...",
    "Confirm Delete": "Xác nhận xóa", "Are you sure you want to delete the key \"{name}\"?": "Bạn có chắc muốn xóa khóa \"{name}\"?",
    "Yes, Delete": "Có, xóa", "Deleted key!": "Đã xóa khóa!", "Delete failed...": "Không thể xóa khóa...",
    "Select a key from the list first": "Hãy chọn một khóa trong danh sách trước.", "Key applied!": "Đã áp dụng khóa!", "Apply failed...": "Không thể áp dụng khóa...",
    Troubleshoot: "Chẩn đoán & khắc phục", "Error Logs": "Nhật ký lỗi", "Tmux Live Log": "Nhật ký Tmux trực tiếp",
    "System Monitor": "Giám sát hệ thống", "Get Help via Discord": "Nhận hỗ trợ qua Discord",
    "Search logs...": "Tìm trong nhật ký...", "Delete All": "Xóa tất cả", "No error logs!": "Không có nhật ký lỗi!",
    Copy: "Sao chép", "Could not load this log.": "Không thể tải nhật ký này.",
    "Failed to load error logs.": "Không thể tải nhật ký lỗi.", "Delete log?": "Xóa nhật ký này?",
    "Delete all error logs?": "Xóa tất cả nhật ký lỗi?", "This cannot be undone.": "Thao tác này không thể hoàn tác.",
    "Log deleted!": "Đã xóa nhật ký!", "All error logs deleted!": "Đã xóa tất cả nhật ký lỗi!",
    "Delete all failed.": "Không thể xóa tất cả.", "Copied to clipboard!": "Đã sao chép vào bộ nhớ tạm!",
    "(waiting for log output…)": "(đang chờ dữ liệu nhật ký…)", Resume: "Tiếp tục", Pause: "Tạm dừng",
    "Capture Log": "Lưu nhật ký", "Current session captured!": "Đã lưu nhật ký phiên hiện tại!", "Capture failed.": "Không thể lưu nhật ký.",
    "Saved Session Logs": "Nhật ký phiên đã lưu", "No tmux logs found.": "Không tìm thấy nhật ký Tmux.",
    "Delete all logs?": "Xóa tất cả nhật ký?", "All logs deleted!": "Đã xóa tất cả nhật ký!", "Deleted!": "Đã xóa!", "Delete failed.": "Không thể xóa.",
    "StarPilot has a vibrant, welcoming community discord. Stop by to chat or ask questions!": "StarPilot có một cộng đồng Discord thân thiện và sôi động. Hãy ghé qua để trò chuyện hoặc đặt câu hỏi!",
    "Quick diagnostics snapshot for weird behavior reports and copy-ready debug logs.": "Tổng hợp nhanh thông tin chẩn đoán để báo cáo lỗi bất thường và cung cấp nhật ký gỡ lỗi có thể sao chép ngay.",
    "Refreshing...": "Đang làm mới...", "Copy to Clipboard": "Sao chép vào bộ nhớ tạm",
    "Only non-default values": "Chỉ hiện các giá trị khác mặc định", "Search settings or categories...": "Tìm cài đặt hoặc danh mục...",
    "Search diagnostics": "Tìm trong dữ liệu chẩn đoán", "Onroad:": "Đang chạy:", Yes: "Có", No: "Không",
    "Changed Settings:": "Các cài đặt đã thay đổi:", "Loading troubleshoot data...": "Đang tải dữ liệu chẩn đoán...",
    "Vehicle Fault Status": "Trạng thái lỗi của xe", Live: "Trực tiếp", Unavailable: "Không khả dụng",
    "Vehicle fault status unavailable.": "Không thể đọc trạng thái lỗi của xe.", Snapshot: "Bản ghi nhanh",
    "No snapshot data.": "Không có dữ liệu ghi nhận.", "Reset to Default": "Khôi phục mặc định", "Resetting...": "Đang khôi phục...",
    "Reset to defaults?": "Khôi phục về mặc định?", Changed: "Đã thay đổi", current: "hiện tại", default: "mặc định", learned: "đã học",
    "No settings are currently different from their defaults.": "Hiện không có cài đặt nào khác với giá trị mặc định.",
    "Troubleshoot data refreshed.": "Đã làm mới dữ liệu chẩn đoán.", "Failed to load troubleshoot data": "Không thể tải dữ liệu chẩn đoán.",
    "Failed to reset section.": "Không thể khôi phục mục này về mặc định.", "Troubleshoot report copied to clipboard.": "Đã sao chép báo cáo chẩn đoán vào bộ nhớ tạm.",
    "Failed to copy report.": "Không thể sao chép báo cáo.",
    Paused: "Đã tạm dừng", "Connection interrupted": "Kết nối bị gián đoạn", "Live updates": "Cập nhật theo thời gian thực",
    "Waiting for the device…": "Đang chờ thiết bị…", "Reading system activity…": "Đang đọc trạng thái hoạt động của hệ thống…",
    "Cannot refresh system monitor. Showing the last captured values.": "Không thể làm mới dữ liệu giám sát hệ thống. Đang hiển thị các giá trị ghi nhận gần nhất.",
    Memory: "Bộ nhớ", Processes: "Tiến trình", "Onboard CPU temperature": "Nhiệt độ CPU trên thiết bị",
    "Onboard GPU temperature": "Nhiệt độ GPU trên thiết bị", "eGPU hotspot temperature": "Nhiệt độ điểm nóng eGPU",
    "eGPU temperature": "Nhiệt độ eGPU", "eGPU VRAM": "VRAM eGPU", "CPU cores": "Số nhân CPU",
    "Search feature, process, PID or user…": "Tìm tính năng, tiến trình, PID hoặc người dùng…",
    "Comma processes": "Tiến trình của comma", "Apps and services": "Ứng dụng & dịch vụ", "All processes": "Tất cả tiến trình",
    "Collecting the first CPU sample…": "Đang thu thập mẫu sử dụng CPU đầu tiên…", Process: "Tiến trình", User: "Người dùng",
    "No matching processes.": "Không tìm thấy tiến trình phù hợp.", Running: "Đang chạy", Sleeping: "Tạm nghỉ",
    Waiting: "Đang chờ", Stopped: "Đã dừng", Tracing: "Đang theo dõi", Zombie: "Zombie", Idle: "Không hoạt động",
    "overall usage": "mức sử dụng tổng thể", cores: "nhân", total: "tổng",
    Controllers: "Thiết bị điều khiển", "Bluetooth Devices": "Thiết bị Bluetooth",
    "Bluetooth service unavailable": "Dịch vụ Bluetooth không khả dụng", "Bluetooth operation failed": "Không thể thực hiện thao tác Bluetooth",
    "Enter the value to continue pairing.": "Nhập mã để tiếp tục ghép nối.",
    "Pairing…": "Đang ghép nối…", "Connected · Audio output": "Đã kết nối · Đầu ra âm thanh", Connected: "Đã kết nối",
    Saved: "Đã lưu", "Ready to pair": "Sẵn sàng ghép nối",
    "Bluetooth On": "Bluetooth đang bật", "Bluetooth Off": "Bluetooth đang tắt", "Turn Off": "Tắt", "Turn On": "Bật",
    "Scanning, pairing, and forgetting devices are available offroad only.": "Chỉ có thể quét, ghép nối hoặc quên thiết bị khi xe đang đỗ.",
    "Pairing request": "Yêu cầu ghép nối", "Confirm the pairing request.": "Xác nhận yêu cầu ghép nối.",
    "Allow this device to connect?": "Cho phép thiết bị này kết nối?", "Enter the PIN supplied by the device.": "Nhập mã PIN do thiết bị cung cấp.",
    "Enter the device passkey.": "Nhập mã xác thực của thiết bị.", Value: "Giá trị", Allow: "Cho phép",
    "Searching…": "Đang tìm kiếm…", "Search for Devices": "Tìm thiết bị", "My Devices": "Thiết bị của tôi",
    "No saved devices yet.": "Chưa có thiết bị nào được lưu.", "Audio · Controller": "Âm thanh · Thiết bị điều khiển", Audio: "Âm thanh",
    Controller: "Thiết bị điều khiển", Disconnect: "Ngắt kết nối", Connect: "Kết nối",
    "Stop Using for Audio": "Ngừng dùng cho âm thanh", "Use for Audio": "Dùng cho âm thanh", "Test Audio": "Kiểm tra âm thanh",
    "Available Devices": "Thiết bị khả dụng", "Searching for nearby devices…": "Đang tìm thiết bị ở gần…",
    "No nearby devices found.": "Không tìm thấy thiết bị nào ở gần.", Pair: "Ghép nối",
    "Wheel controls are unavailable": "Nút điều khiển trên vô lăng không khả dụng", "Wheel control operation failed": "Không thể thực hiện thao tác điều khiển trên vô lăng",
    "Mappings can only be changed while offroad. Mapped buttons continue working onroad.": "Chỉ có thể thay đổi cách gán nút khi xe đang đỗ. Các nút đã gán vẫn hoạt động khi xe đang chạy.",
    "The wheel control service is starting.": "Dịch vụ nút điều khiển trên vô lăng đang khởi động.",
    "Stop Testing": "Dừng kiểm tra", "Test Buttons": "Kiểm tra nút", "Clear All": "Xóa tất cả",
    "Disconnect controllers when offroad": "Ngắt kết nối thiết bị điều khiển khi xe đang đỗ",
    "After two minutes offroad, paired controllers disconnect to save battery and reconnect when the car starts. Bluetooth and audio-only devices stay connected.": "Sau khi xe đỗ được hai phút, các thiết bị điều khiển đã ghép nối sẽ tự ngắt kết nối để tiết kiệm pin và kết nối lại khi xe khởi động. Bluetooth và các thiết bị chỉ dùng cho âm thanh vẫn duy trì kết nối.",
    Successful: "Thành công", "Not mapped": "Chưa gán", "External input": "Thiết bị nhập bên ngoài",
    "is mapped to slot": "được gán vào vị trí", "has no mapping": "chưa được gán",
    "Connected input devices": "Thiết bị nhập đã kết nối",
    "Favorite buttons are the default, with controller-only actions below. Only the selected gamepad controls Joystick Mode.": "Các nút Yêu thích được dùng mặc định. Bên dưới là các thao tác riêng cho thiết bị điều khiển. Chỉ tay cầm đang được chọn mới có thể điều khiển Chế độ Joystick.",
    "Buttons and joystick axes": "Nút và trục joystick", "Buttons only": "Chỉ dùng nút",
    "Enabled for Joystick Mode": "Đã bật cho Chế độ Joystick", "Enable for Joystick Mode": "Bật cho Chế độ Joystick",
    "Connect or pair a controller, macropad, or keyboard.": "Kết nối hoặc ghép nối tay cầm, macropad hoặc bàn phím.",
    "On-screen Favorites": "Nút Yêu thích trên màn hình", "Favorite #": "Nút Yêu thích #", "Not configured": "Chưa cấu hình",
    "Learn Button": "Gán nút", "Listening...": "Đang chờ nhấn nút...", "Listening (": "Đang chờ nhấn nút (",
    "Remove mapping": "Hủy gán nút", "Choose and enable these slots in Toggles to map buttons to them.": "Chọn và bật các vị trí này trong Tùy chọn để gán nút cho chúng.",
    "Controller-only Actions": "Thao tác riêng cho thiết bị điều khiển",
    "Ten additional actions for physical buttons. These never appear as on-screen Favorites.": "Mười thao tác bổ sung dành cho các nút vật lý. Các thao tác này sẽ không xuất hiện trong mục Yêu thích trên màn hình.",
    "Controller Action #": "Thao tác điều khiển #", Configured: "Đã cấu hình", "Choose action": "Chọn thao tác",
    "Set speed": "Đặt tốc độ", "Press one button on your controller, macropad, or keyboard.": "Nhấn một nút trên tay cầm, macropad hoặc bàn phím.",
    Button: "Nút",
    "Software & Updates": "Phần mềm & cập nhật", "Backup & Restore": "Sao lưu & khôi phục", "Danger Zone": "Vùng nguy hiểm",
    "Loading update info...": "Đang tải thông tin cập nhật...",
    "Updates and branch switching are only available while offroad.": "Chỉ có thể cập nhật và chuyển nhánh khi xe đang đỗ.",
    "Device rebooting": "Thiết bị đang khởi động lại", "Device reconnected": "Thiết bị đã kết nối lại",
    "The device is temporarily offline. Galaxy will keep checking until it reconnects.": "Thiết bị đang tạm thời ngoại tuyến. Galaxy sẽ tiếp tục kiểm tra cho đến khi thiết bị kết nối lại.",
    "The update is complete. Waiting for the device to reconnect…": "Đã cập nhật xong. Đang chờ thiết bị kết nối lại…",
    "Galaxy is connected again and the update status is current.": "Galaxy đã kết nối lại và trạng thái cập nhật đã được đồng bộ.",
    "Update Status": "Trạng thái cập nhật", "Reconnecting…": "Đang kết nối lại…", "Up to date": "Đã ở bản mới nhất", "Not checked": "Chưa kiểm tra",
    "Installed branch": "Nhánh hiện tại", Stage: "Giai đoạn", Local: "Cục bộ", Remote: "Từ xa",
    "Show recent commits on GitHub": "Xem các commit gần đây trên GitHub", "Update progress": "Tiến trình cập nhật", Updating: "Đang cập nhật",
    "Waiting for the device to reconnect. The last update status is being kept on screen.": "Đang chờ thiết bị kết nối lại. Trạng thái cập nhật gần nhất vẫn được hiển thị.",
    "Automatically Install Updates": "Tự động cài đặt bản cập nhật",
    "Install updates automatically while parked with an active internet connection.": "Tự động cài đặt bản cập nhật khi xe đang đỗ và có kết nối Internet.",
    "Advanced update options": "Tùy chọn cập nhật nâng cao", "Install a Version": "Cài phiên bản cụ thể",
    "Most people should stay on the latest version. Use this only to install a specific branch or historical version while troubleshooting.": "Hầu hết người dùng nên sử dụng phiên bản mới nhất. Chỉ dùng mục này khi cần cài một nhánh cụ thể hoặc phiên bản cũ để chẩn đoán sự cố.",
    "Installed branch:": "Nhánh hiện tại:", "Target branch": "Nhánh cần cài", "Select a branch": "Chọn nhánh",
    "Stable releases. Recommended for most users.": "Các bản phát hành ổn định. Khuyến nghị cho hầu hết người dùng.",
    "StarPilot — Release": "StarPilot — Bản phát hành",
    "Latest features and fixes under development. Updates regularly and may introduce bugs.": "Các tính năng và bản sửa lỗi mới nhất đang trong quá trình phát triển. Được cập nhật thường xuyên và có thể phát sinh lỗi.",
    "Dom — Development": "Dom — Bản phát triển", "Other branches…": "Các nhánh khác…", "Other branches": "Các nhánh khác",
    "Additional branches from this installation's repository.": "Các nhánh khác có trong kho mã của bản cài đặt này.",
    "Select another branch": "Chọn nhánh khác", "No other branches available": "Không có nhánh nào khác", "(current)": "(hiện tại)",
    "The full branch list is temporarily unavailable. Standard branches are shown; selecting a version still verifies it with the device.": "Danh sách đầy đủ các nhánh hiện không khả dụng. Các nhánh tiêu chuẩn vẫn được hiển thị và phiên bản được chọn vẫn sẽ được thiết bị xác minh.",
    Latest: "Mới nhất", "Choose earlier…": "Chọn phiên bản cũ hơn…",
    "This installed branch is no longer listed by the repository. Select an available target branch to install a version.": "Nhánh đang cài đặt không còn được liệt kê trong kho mã. Hãy chọn một nhánh khả dụng để cài đặt phiên bản.",
    "Starting installation…": "Đang bắt đầu cài đặt…", "Install selected version": "Cài phiên bản đã chọn",
    "Pinned version:": "Phiên bản đã ghim:", "Automatic updates were paused at installation.": "Cập nhật tự động đã được tạm dừng khi cài phiên bản này.",
    "Return to Latest": "Quay lại bản mới nhất", Checking: "Đang kiểm tra", "Check for Updates": "Kiểm tra cập nhật",
    "Update Now": "Cập nhật ngay", Recover: "Tiếp tục cập nhật", Rollback: "Quay về bản trước",
    "Check for Updates scans for a newer commit. Use Update Now to install it.": "Kiểm tra cập nhật sẽ tìm commit mới hơn. Chọn Cập nhật ngay để cài đặt.",
    "Recover continues an update that was interrupted (for example, by power loss mid-install). Rollback returns the device to the previously installed version if the current one has a problem.": "Tiếp tục cập nhật sẽ tiếp tục quá trình cập nhật bị gián đoạn, ví dụ do mất nguồn trong khi cài đặt. Quay về bản trước sẽ đưa thiết bị trở lại phiên bản đã cài trước đó nếu phiên bản hiện tại gặp sự cố.",
    "The device is up to date. Update becomes available only after a check finds a newer commit.": "Thiết bị đã ở bản mới nhất. Tùy chọn cập nhật chỉ khả dụng sau khi kiểm tra phát hiện commit mới hơn.",
    "Settings Profiles": "Hồ sơ cài đặt",
    "Keep two local configurations for different vehicles, drivers, or troubleshooting. Profiles never include pairing or sensitive device data.": "Lưu hai cấu hình cục bộ để sử dụng cho các xe, tài xế hoặc mục đích chẩn đoán khác nhau. Hồ sơ không bao gồm thông tin ghép nối hoặc dữ liệu nhạy cảm của thiết bị.",
    "Park the vehicle to save or load a profile.": "Xe phải đang đỗ để lưu hoặc áp dụng hồ sơ.", Damaged: "Bị lỗi", Empty: "Trống", settings: "cài đặt",
    "Saving...": "Đang lưu...", Overwrite: "Ghi đè", "Save Current": "Lưu cấu hình hiện tại", Load: "Áp dụng",
    "This replaces the settings currently stored in this slot.": "Thao tác này sẽ ghi đè các cài đặt hiện có trong vị trí lưu này.",
    "This applies every saved setting in the slot to the device.": "Thao tác này sẽ áp dụng tất cả cài đặt đã lưu trong vị trí này lên thiết bị.",
    "Load Settings": "Áp dụng cài đặt", "Failed to save settings profile.": "Không thể lưu hồ sơ cài đặt.",
    "Failed to load settings profile.": "Không thể áp dụng hồ sơ cài đặt.",
    "Backup Toggles": "Sao lưu tùy chọn", "Restore Toggles": "Khôi phục tùy chọn",
    "Delete All Driving Routes": "Xóa toàn bộ dữ liệu hành trình",
    "Backup downloads your toggle settings as a JSON file. Restore re-applies one, and Reset to Default clears them back to stock and reboots.": "Sao lưu sẽ tải các cài đặt tùy chọn xuống dưới dạng tệp JSON. Khôi phục sẽ áp dụng lại bản sao lưu, còn Khôi phục mặc định sẽ đưa các tùy chọn về thiết lập gốc và khởi động lại thiết bị.",
    "Factory Reset Status": "Trạng thái khôi phục cài đặt gốc", "Last Error:": "Lỗi gần nhất:",
    "Toggle backup downloaded.": "Đã tải xuống bản sao lưu tùy chọn.", "Backup failed.": "Không thể sao lưu.",
    "That toggle backup file is too large.": "Tệp sao lưu tùy chọn quá lớn.",
    "That file is not a valid toggle backup.": "Tệp này không phải là bản sao lưu tùy chọn hợp lệ.",
    "Toggles restored!": "Đã khôi phục tùy chọn!", "Failed to restore toggles.": "Không thể khôi phục tùy chọn.",
    "Reset toggles to default?": "Đặt lại tất cả tùy chọn về mặc định?",
    "This resets all toggles to their default values and reboots.": "Thao tác này sẽ đưa tất cả tùy chọn về giá trị mặc định và khởi động lại thiết bị.",
    "Resetting toggles to default... rebooting.": "Đang đặt lại các tùy chọn về mặc định... thiết bị sẽ khởi động lại.",
    "Reset failed.": "Không thể đặt lại.",
    "Install selected version?": "Cài phiên bản đã chọn?", "Install & Reboot": "Cài đặt & khởi động lại",
    "Your device will reboot when installation finishes.": "Thiết bị sẽ khởi động lại sau khi cài đặt hoàn tất.",
    "This uses the normal branch updater, including any required OS update during startup. Your automatic-update setting is unchanged.": "Thao tác này sử dụng trình cập nhật nhánh thông thường, bao gồm cập nhật hệ điều hành nếu cần trong quá trình khởi động. Cài đặt cập nhật tự động sẽ không thay đổi.",
    "Automatic updates will be paused for this earlier version.": "Cập nhật tự động sẽ được tạm dừng khi sử dụng phiên bản cũ này.",
    "This replaces the current software. Settings and statistics are kept, and local code changes are backed up. Older versions may remove this picker; an SSH recovery copy is saved on the device.": "Thao tác này sẽ thay thế phần mềm hiện tại. Các cài đặt và thống kê vẫn được giữ lại, đồng thời các thay đổi mã nguồn cục bộ sẽ được sao lưu. Một số phiên bản cũ có thể không còn trình chọn phiên bản này; bản sao khôi phục qua SSH sẽ được lưu trên thiết bị.",
    "Installation is unavailable while driving or updating, or the selection has changed.": "Không thể cài đặt khi xe đang chạy, thiết bị đang cập nhật hoặc lựa chọn phiên bản đã thay đổi.",
    "Installation failed.": "Không thể cài đặt.", "An update is already running.": "Đã có một quá trình cập nhật đang chạy.",
    "Update available.": "Có bản cập nhật.", "No update available — you're up to date.": "Không có bản cập nhật mới — thiết bị đã ở bản mới nhất.",
    "Failed to check for updates.": "Không thể kiểm tra cập nhật.",
    "Automatic updates enabled.": "Đã bật cập nhật tự động.", "Automatic updates disabled.": "Đã tắt cập nhật tự động.",
    "Failed to update Automatic Updates.": "Không thể thay đổi cài đặt cập nhật tự động.",
    "Fast update is already running.": "Quá trình cập nhật nhanh đang chạy.",
    "No update available. Run \"Check for Updates\" first.": "Không có bản cập nhật. Hãy chọn \"Kiểm tra cập nhật\" trước.",
    "Update & Reboot": "Cập nhật & khởi động lại",
    "Your device will reboot when the update is done.": "Thiết bị sẽ khởi động lại sau khi cập nhật hoàn tất.",
    "No previous installed version is available to roll back to.": "Không có phiên bản đã cài trước đó để quay về.",
    "Recover the interrupted update?": "Tiếp tục bản cập nhật bị gián đoạn?",
    "Roll back to the previous installed version?": "Quay về phiên bản đã cài trước đó?",
    "Continue?": "Tiếp tục?", Continue: "Tiếp tục",
    "Your device will reboot when the operation is done.": "Thiết bị sẽ khởi động lại sau khi thao tác hoàn tất.",
    "Update started.": "Đã bắt đầu cập nhật.", "Update failed.": "Không thể cập nhật.",
    "Factory reset (SAVE ME)?": "Khôi phục cài đặt gốc (SAVE ME)?",
    "This wipes params, backups, themes, models, maps, and route data, then reboots. This cannot be undone.": "Thao tác này sẽ xóa tham số, bản sao lưu, giao diện, mô hình, bản đồ và dữ liệu hành trình, sau đó khởi động lại thiết bị. Không thể hoàn tác.",
    "Factory Reset": "Khôi phục cài đặt gốc", "SAVE ME initiated — factory resetting...": "Đã bắt đầu SAVE ME — đang khôi phục cài đặt gốc...",
    "Factory reset failed.": "Không thể khôi phục cài đặt gốc.",
    "This permanently deletes all local routes from standard, high-resolution, and alternate footage storage. It does not reset settings or reboot the device.": "Thao tác này sẽ xóa vĩnh viễn toàn bộ dữ liệu hành trình cục bộ, bao gồm video tiêu chuẩn, video độ phân giải cao và video thay thế. Cài đặt sẽ không bị đặt lại và thiết bị sẽ không khởi động lại.",
    "Delete Routes": "Xóa dữ liệu hành trình", "All local driving routes deleted.": "Đã xóa toàn bộ dữ liệu hành trình cục bộ.",
    "Failed to delete driving routes.": "Không thể xóa dữ liệu hành trình.",
    "Tailscale creates a secure, private connection between your openpilot device and your phone or PC so you can access and control it from anywhere!": "Tailscale tạo kết nối riêng tư và bảo mật giữa thiết bị openpilot với điện thoại hoặc PC, cho phép bạn truy cập và điều khiển thiết bị từ bất kỳ đâu!",
    "Checking Tailscale install status...": "Đang kiểm tra trạng thái cài đặt Tailscale...",
    "Not recommended. Using Galaxy Tunnel is the preferred remote connection method.": "Không khuyến nghị. Galaxy Tunnel là phương thức kết nối từ xa được ưu tiên.",
    "Installing...": "Đang cài đặt...", "Install Tailscale": "Cài Tailscale", "Uninstalling...": "Đang gỡ cài đặt...",
    "Uninstall Tailscale": "Gỡ Tailscale", "Download for your other devices": "Tải xuống cho các thiết bị khác",
    "Installing opens the Tailscale login page to authenticate this device.": "Quá trình cài đặt sẽ mở trang đăng nhập Tailscale để xác thực thiết bị này.",
    "Install started...": "Đã bắt đầu cài đặt...", "Tailscale setup started.": "Đã bắt đầu thiết lập Tailscale.",
    "Failed to install Tailscale.": "Không thể cài đặt Tailscale.", "Uninstall Tailscale?": "Gỡ cài đặt Tailscale?",
    "Uninstall started...": "Đã bắt đầu gỡ cài đặt...", "Tailscale uninstalled.": "Đã gỡ cài đặt Tailscale.",
    "Failed to uninstall Tailscale.": "Không thể gỡ cài đặt Tailscale.",
    "Last resort only. Factory Reset (also known as SAVE ME) wipes params, backups, themes, models, maps, and route data, then reboots the device. This cannot be undone.": "Chỉ sử dụng khi không còn cách nào khác. Khôi phục cài đặt gốc, còn được gọi là SAVE ME, sẽ xóa tham số, bản sao lưu, giao diện, mô hình, bản đồ và dữ liệu hành trình, sau đó khởi động lại thiết bị. Không thể hoàn tác.",
    "Last resort only. ": "Chỉ sử dụng khi không còn cách nào khác. ",
    "Factory Reset (also known as SAVE ME)": "Khôi phục cài đặt gốc (còn được gọi là SAVE ME)",
    " wipes params, backups, themes, models, maps, and route data, then reboots the device. This cannot be undone.": " sẽ xóa tham số, bản sao lưu, giao diện, mô hình, bản đồ và dữ liệu hành trình, sau đó khởi động lại thiết bị. Không thể hoàn tác.",
    "Factory Reset Device (SAVE ME)": "Khôi phục cài đặt gốc cho thiết bị (SAVE ME)",
    "Could not refresh the full branch list. Standard branches are still available.": "Không thể làm mới toàn bộ danh sách nhánh. Các nhánh tiêu chuẩn vẫn khả dụng.",
  },
}

Object.keys(MORE_TRANSLATIONS).forEach((code) => Object.assign(TRANSLATIONS[code], MORE_TRANSLATIONS[code]))

// A word-level fallback covers the many device-provided descriptions and the
// older Galaxy views that still contain literal English labels. Exact phrases
// above always win; this fallback only runs for a non-English selection.
const TERM_TRANSLATIONS = {
  es: {
    "Advanced": "Avanzado", "Always On": "Siempre activo", "Lateral": "Lateral", "Steering": "Dirección", "Longitudinal": "Longitudinal", "Speed": "Velocidad", "Following": "Seguimiento", "Vision": "Visión", "Limits": "Límites", "Visual": "Visual", "Display": "Pantalla", "Sounds": "Sonidos", "Alerts": "Alertas", "Vehicle": "Vehículo", "Wheel": "Volante", "Controls": "Controles", "Device": "Dispositivo", "Data": "Datos", "Developer": "Desarrollador", "Favorites": "Favoritos", "Main": "Principal", "Tools": "Herramientas", "Recordings": "Grabaciones", "Cameras": "Cámaras", "Monitoring": "monitoreo", "Logs": "Registros", "Diagnostics": "diagnósticos", "Model": "Modelo", "Manager": "administrador", "Navigation": "Navegación", "Maps": "mapas", "System": "Sistema", "Laboratory": "Laboratorio", "Plots": "Gráficas", "Testing": "Pruebas", "Ground": "Área", "Theme": "Tema", "Maker": "creador", "Home": "Inicio", "Toggles": "Interruptores", "Install": "Instalar", "Update": "Actualizar", "Available": "disponible", "Loading": "Cargando", "Error": "Error", "Retry": "Reintentar", "Save": "Guardar", "Cancel": "Cancelar", "Close": "Cerrar", "Delete": "Eliminar", "All": "todo", "Search": "Buscar", "Clear": "Borrar", "Manage": "Administrar", "Connected": "Conectado", "Disconnect": "Desconectar", "Connect": "Conectar", "Pair": "Emparejar", "Refresh": "Actualizar", "Status": "Estado", "Samples": "Muestras", "Duration": "Duración", "Distance": "Distancia", "drives": "viajes", "hours": "horas", "engaged": "activado", "Onroad": "En carretera", "Offroad": "Fuera de carretera", "Enabled": "Activado", "Disabled": "Desactivado", "Default": "Predeterminado", "Working": "Procesando", "Run": "Ejecutar", "Reset": "Restablecer", "Download": "Descargar", "Network": "Red", "Current": "Actual", "Change": "Cambiar", "Changes": "Cambios", "Allow": "Permitir", "Use": "Usar", "Show": "Mostrar", "Hide": "Ocultar", "Enable": "Activar", "Disable": "Desactivar", "Automatic": "Automático", "Settings": "Configuración", "Language": "Idioma", "Routes": "Rutas", "Selected": "Seleccionada", "Recommended": "Recomendada", "Alternative": "Alternativa",
  },
  fr: {
    "Advanced": "Avancé", "Always On": "Toujours actif", "Lateral": "Latéral", "Steering": "Direction", "Longitudinal": "Longitudinal", "Speed": "Vitesse", "Following": "Suivi", "Vision": "Vision", "Limits": "Limites", "Visual": "Visuel", "Display": "Affichage", "Sounds": "Sons", "Alerts": "Alertes", "Vehicle": "Véhicule", "Wheel": "Volant", "Controls": "Commandes", "Device": "Appareil", "Data": "Données", "Developer": "Développeur", "Favorites": "Favoris", "Main": "Principal", "Tools": "Outils", "Recordings": "Enregistrements", "Cameras": "Caméras", "Monitoring": "surveillance", "Logs": "Journaux", "Diagnostics": "diagnostics", "Model": "Modèle", "Manager": "gestionnaire", "Navigation": "Navigation", "Maps": "cartes", "System": "Système", "Laboratory": "Laboratoire", "Plots": "Graphiques", "Testing": "Tests", "Ground": "Zone", "Theme": "Thème", "Maker": "créateur", "Home": "Accueil", "Toggles": "Options", "Install": "Installer", "Update": "Mettre à jour", "Available": "disponible", "Loading": "Chargement", "Error": "Erreur", "Retry": "Réessayer", "Save": "Enregistrer", "Cancel": "Annuler", "Close": "Fermer", "Delete": "Supprimer", "All": "tout", "Search": "Rechercher", "Clear": "Effacer", "Manage": "Gérer", "Connected": "Connecté", "Disconnect": "Déconnecter", "Connect": "Connecter", "Pair": "Associer", "Refresh": "Actualiser", "Status": "État", "Samples": "Échantillons", "Duration": "Durée", "Distance": "Distance", "drives": "trajets", "hours": "heures", "engaged": "activé", "Onroad": "En route", "Offroad": "Hors route", "Enabled": "Activé", "Disabled": "Désactivé", "Default": "Par défaut", "Working": "En cours", "Run": "Exécuter", "Reset": "Réinitialiser", "Download": "Télécharger", "Network": "Réseau", "Current": "Actuel", "Change": "Modifier", "Changes": "Modifications", "Allow": "Autoriser", "Use": "Utiliser", "Show": "Afficher", "Hide": "Masquer", "Enable": "Activer", "Disable": "Désactiver", "Automatic": "Automatique", "Settings": "Paramètres", "Language": "Langue", "Routes": "Itinéraires", "Selected": "Sélectionné", "Recommended": "Recommandé", "Alternative": "Alternative",
  },
  ko: {
    "Advanced": "고급", "Always On": "항상 활성화", "Lateral": "횡방향", "Steering": "조향", "Longitudinal": "종방향", "Speed": "속도", "Following": "추종", "Vision": "비전", "Limits": "제한", "Visual": "시각", "Display": "디스플레이", "Sounds": "소리", "Alerts": "경고", "Vehicle": "차량", "Wheel": "휠", "Controls": "제어", "Device": "장치", "Data": "데이터", "Developer": "개발자", "Favorites": "즐겨찾기", "Main": "메인", "Tools": "도구", "Recordings": "녹화", "Cameras": "카메라", "Monitoring": "모니터링", "Logs": "로그", "Diagnostics": "진단", "Model": "모델", "Manager": "관리자", "Navigation": "내비게이션", "Maps": "지도", "System": "시스템", "Laboratory": "연구소", "Plots": "플롯", "Testing": "테스트", "Ground": "공간", "Theme": "테마", "Maker": "제작", "Home": "홈", "Toggles": "토글", "Install": "설치", "Update": "업데이트", "Available": "사용 가능", "Loading": "로드 중", "Error": "오류", "Retry": "재시도", "Save": "저장", "Cancel": "취소", "Close": "닫기", "Delete": "삭제", "All": "모두", "Search": "검색", "Clear": "지우기", "Manage": "관리", "Connected": "연결됨", "Disconnect": "연결 해제", "Connect": "연결", "Pair": "페어링", "Refresh": "새로 고침", "Status": "상태", "Samples": "샘플", "Duration": "시간", "Distance": "거리", "drives": "주행", "hours": "시간", "engaged": "활성화", "Onroad": "주행 중", "Offroad": "오프로드", "Enabled": "활성화", "Disabled": "비활성화", "Default": "기본값", "Working": "처리 중", "Run": "실행", "Reset": "초기화", "Download": "다운로드", "Network": "네트워크", "Current": "현재", "Change": "변경", "Changes": "변경 사항", "Allow": "허용", "Use": "사용", "Show": "표시", "Hide": "숨기기", "Enable": "활성화", "Disable": "비활성화", "Automatic": "자동", "Settings": "설정", "Language": "언어", "Routes": "경로", "Selected": "선택됨", "Recommended": "추천", "Alternative": "대안",
  },
  "zh-CHS": {
    "Advanced": "高级", "Always On": "始终启用", "Lateral": "横向", "Steering": "转向", "Longitudinal": "纵向", "Speed": "速度", "Following": "跟车", "Vision": "视觉", "Limits": "限制", "Visual": "视觉", "Display": "显示", "Sounds": "声音", "Alerts": "提醒", "Vehicle": "车辆", "Wheel": "方向盘", "Controls": "控制", "Device": "设备", "Data": "数据", "Developer": "开发者", "Favorites": "收藏", "Main": "主菜单", "Tools": "工具", "Recordings": "录制", "Cameras": "摄像头", "Monitoring": "监控", "Logs": "日志", "Diagnostics": "诊断", "Model": "模型", "Manager": "管理器", "Navigation": "导航", "Maps": "地图", "System": "系统", "Laboratory": "实验室", "Plots": "图表", "Testing": "测试", "Ground": "区域", "Theme": "主题", "Maker": "制作器", "Home": "主页", "Toggles": "开关", "Install": "安装", "Update": "更新", "Available": "可用", "Loading": "加载中", "Error": "错误", "Retry": "重试", "Save": "保存", "Cancel": "取消", "Close": "关闭", "Delete": "删除", "All": "全部", "Search": "搜索", "Clear": "清除", "Manage": "管理", "Connected": "已连接", "Disconnect": "断开连接", "Connect": "连接", "Pair": "配对", "Refresh": "刷新", "Status": "状态", "Samples": "样本", "Duration": "时长", "Distance": "距离", "drives": "驾驶次数", "hours": "小时", "engaged": "已启用", "Onroad": "行驶中", "Offroad": "非行驶", "Enabled": "已启用", "Disabled": "已停用", "Default": "默认", "Working": "处理中", "Run": "运行", "Reset": "重置", "Download": "下载", "Network": "网络", "Current": "当前", "Change": "更改", "Changes": "更改内容", "Allow": "允许", "Use": "使用", "Show": "显示", "Hide": "隐藏", "Enable": "启用", "Disable": "停用", "Automatic": "自动", "Settings": "设置", "Language": "语言", "Routes": "路线", "Selected": "已选择", "Recommended": "推荐", "Alternative": "备选",
  },
  vi: {
    "Advanced": "Nâng cao", "Always On": "Luôn bật", "Lateral": "Điều khiển ngang", "Steering": "Đánh lái", "Longitudinal": "Điều khiển dọc", "Speed": "Tốc độ", "Following": "Khoảng cách", "Vision": "Camera", "Limits": "Giới hạn", "Visual": "Hiển thị", "Display": "Màn hình", "Sounds": "Âm thanh", "Alerts": "Cảnh báo", "Vehicle": "Xe", "Wheel": "Vô lăng", "Controls": "Điều khiển", "Device": "Thiết bị", "Data": "Dữ liệu", "Developer": "Nhà phát triển", "Favorites": "Yêu thích", "Main": "Chính", "Tools": "Công cụ", "Recordings": "Bản ghi", "Cameras": "Camera", "Monitoring": "giám sát", "Logs": "Nhật ký", "Diagnostics": "chẩn đoán", "Model": "Mô hình", "Manager": "quản lý", "Navigation": "Điều hướng", "Maps": "bản đồ", "System": "Hệ thống", "Laboratory": "Phòng thí nghiệm", "Plots": "Biểu đồ", "Testing": "Thử nghiệm", "Ground": "Khu vực", "Theme": "Giao diện", "Maker": "tạo", "Home": "Trang chủ", "Toggles": "Tùy chọn", "Install": "Cài đặt", "Update": "Cập nhật", "Available": "có sẵn", "Loading": "Đang tải", "Error": "Lỗi", "Retry": "Thử lại", "Save": "Lưu", "Cancel": "Hủy", "Close": "Đóng", "Delete": "Xóa", "All": "tất cả", "Search": "Tìm kiếm", "Clear": "Xóa", "Manage": "Quản lý", "Connected": "Đã kết nối", "Disconnect": "Ngắt kết nối", "Connect": "Kết nối", "Pair": "Ghép nối", "Refresh": "Làm mới", "Status": "Trạng thái", "Samples": "Mẫu", "Duration": "Thời lượng", "Distance": "Khoảng cách", "drives": "chuyến đi", "hours": "giờ", "engaged": "đang hoạt động", "Onroad": "Đang chạy", "Offroad": "Đang đỗ", "Enabled": "Đã bật", "Disabled": "Đã tắt", "Default": "Mặc định", "Working": "Đang xử lý", "Run": "Chạy", "Reset": "Đặt lại", "Download": "Tải xuống", "Network": "Mạng", "Current": "Hiện tại", "Change": "Thay đổi", "Changes": "Thay đổi", "Allow": "Cho phép", "Use": "Dùng", "Show": "Hiện", "Hide": "Ẩn", "Enable": "Bật", "Disable": "Tắt", "Automatic": "Tự động", "Settings": "Cài đặt", "Language": "Ngôn ngữ",     "Routes": "Tuyến đường", "Selected": "Đã chọn", "Recommended": "Đề xuất", "Alternative": "Tuyến khác",
    "Analyzing": "Đang phân tích", "drive": "chuyến", "queued": "đang chờ", "distracted": "mất tập trung", "unresponsive": "không phản hồi",
    "duration": "thời lượng", "analyzing": "đang phân tích", "speed": "tốc độ", "Pending": "Đang chờ", "Excluded": "Không tính",
    "segments": "đoạn", "unknown": "không rõ", "used of": "đã dùng trên tổng", "Online": "Trực tuyến", "Offline": "Ngoại tuyến",
    "miles": "dặm", "kilometers": "km", "avg": "TB", "using this model": "sử dụng mô hình này",
    "device online": "thiết bị đang trực tuyến", "device offline": "thiết bị đang ngoại tuyến", "Parked": "Đang đỗ",
    "Troubleshoot": "Chẩn đoán & khắc phục", "Live": "Trực tiếp", "Unavailable": "Không khả dụng", "Changed": "Đã thay đổi",
    "current": "hiện tại", "default": "mặc định", "learned": "đã học", "Paused": "Đã tạm dừng", "Resume": "Tiếp tục", "Pause": "Tạm dừng",
    "Yes": "Có", "No": "Không", "Copy": "Sao chép", "Running": "Đang chạy", "Sleeping": "Tạm nghỉ", "Waiting": "Đang chờ",
    "Stopped": "Đã dừng", "Idle": "Không hoạt động", "Memory": "Bộ nhớ", "Processes": "Tiến trình", "Process": "Tiến trình", "User": "Người dùng",
  },
}

Object.assign(TERM_TRANSLATIONS.es, { Route: "Ruta", Select: "Seleccionar" })
Object.assign(TERM_TRANSLATIONS.fr, { Route: "Itinéraire", Select: "Sélectionner" })
Object.assign(TERM_TRANSLATIONS.ko, { Route: "경로", Select: "선택" })
Object.assign(TERM_TRANSLATIONS["zh-CHS"], { Route: "路线", Select: "选择" })
Object.assign(TERM_TRANSLATIONS.vi, { Route: "Tuyến đường", Select: "Chọn" })

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

const TERM_REPLACERS = Object.fromEntries(Object.entries(TERM_TRANSLATIONS).map(([code, terms]) => [
  code,
  Object.entries(terms)
    .sort(([a], [b]) => b.length - a.length)
    .map(([source, target]) => [new RegExp(`(^|[^A-Za-z])${escapeRegExp(source)}(?=$|[^A-Za-z])`, "gi"), target, source.match(/[A-Za-z]+/g)?.length || 1]),
]))

function translateText(value) {
  const source = String(value ?? "")
  const exact = TRANSLATIONS[languageState?.code]?.[source]
  if (exact) return exact
  if (!languageState || languageState.code === "en" || /https?:\/\//i.test(source)) return source
  let translated = source
  let replacedWords = 0
  for (const [pattern, replacement, wordCount] of TERM_REPLACERS[languageState.code] || []) {
    translated = translated.replace(pattern, (_, prefix) => {
      replacedWords += wordCount
      return `${prefix}${replacement}`
    })
  }
  const sourceWords = source.match(/[A-Za-z]+/g)?.length || 0
  return sourceWords >= 4 && replacedWords / sourceWords < 0.8 ? source : translated
}

function storageValue() {
  try { return window.localStorage.getItem(STORAGE_KEY) || "en" } catch (e) { return "en" }
}

export function normalizeLanguage(value) {
  const code = String(value || "").trim().replace(/^main_/i, "")
  return SUPPORTED_CODES.has(code) ? code : "en"
}

export const languageState = reactive({ code: normalizeLanguage(storageValue()) })

const translatedTextNodes = new WeakMap()
const translatedAttributes = new WeakMap()
let domObserver = null
const TRANSLATABLE_ATTRIBUTES = ["aria-label", "placeholder", "title"]

function canTranslateNode(node) {
  const parent = node?.parentElement
  return !!parent && !parent.closest("script, style, textarea, pre, [data-no-translate]")
}

function translateTextNode(node) {
  if (!canTranslateNode(node)) return
  const current = node.nodeValue || ""
  if (!current.trim()) return
  let state = translatedTextNodes.get(node)
  if (!state) {
    state = { source: current, output: current }
    translatedTextNodes.set(node, state)
  } else if (current !== state.output) {
    // Vue replaced the source text (for example, a device-provided label).
    state.source = current
  }
  const output = translateText(state.source)
  if (output !== current) node.nodeValue = output
  state.output = output
}

function translateElementAttributes(element) {
  if (!element || element.matches("script, style, textarea, pre, [data-no-translate]")) return
  let state = translatedAttributes.get(element)
  if (!state) {
    state = {}
    translatedAttributes.set(element, state)
  }
  for (const attribute of TRANSLATABLE_ATTRIBUTES) {
    if (!element.hasAttribute(attribute)) continue
    const current = element.getAttribute(attribute) || ""
    const previous = state[attribute]
    if (!previous) state[attribute] = { source: current, output: current }
    else if (current !== previous.output) previous.source = current
    const entry = state[attribute]
    const output = translateText(entry.source)
    if (output !== current) element.setAttribute(attribute, output)
    entry.output = output
  }
}

export function translateDom(root = (typeof document !== "undefined" ? document.getElementById("galaxy-app") : null)) {
  if (!root || typeof document === "undefined") return
  translateElementAttributes(root)
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  let node
  while ((node = walker.nextNode())) translateTextNode(node)
  root.querySelectorAll("*").forEach(translateElementAttributes)
}

export function installDomTranslator(root = (typeof document !== "undefined" ? document.getElementById("galaxy-app") : null)) {
  if (!root || typeof MutationObserver === "undefined") return
  translateDom(root)
  domObserver?.disconnect()
  domObserver = new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === "characterData") translateTextNode(record.target)
      else if (record.type === "attributes") translateElementAttributes(record.target)
      else record.addedNodes.forEach((node) => {
        if (node.nodeType === Node.TEXT_NODE) translateTextNode(node)
        else if (node.nodeType === Node.ELEMENT_NODE) translateDom(node)
      })
    }
  })
  domObserver.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: TRANSLATABLE_ATTRIBUTES })
}

export function setLanguage(value) {
  const code = normalizeLanguage(value)
  languageState.code = code
  try { window.localStorage.setItem(STORAGE_KEY, code) } catch (e) { /* storage can be unavailable in private webviews */ }
  if (typeof document !== "undefined") document.documentElement.lang = code === "zh-CHS" ? "zh-CN" : code
  if (typeof document !== "undefined") translateDom(document.getElementById("galaxy-app"))
  return code
}

export function t(key, fallback = key) {
  const source = String(key ?? "")
  const translated = translateText(source)
  return translated !== source ? translated : fallback || source
}

setLanguage(languageState.code)
