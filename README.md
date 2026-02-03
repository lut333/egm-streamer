# EGM Streamer & State Detector (IPC Version)

專為工控機 (IPC) 設計的雙路推流與遊戲狀態偵測系統。
整合了 **RTMP 推流** (Game + Camera) 與 **遊戲狀態偵測** (Image Hash + ROI) 功能，並提供 **Web 管理介面**。

## 特色

- **低延遲推流**: 專為即時互動優化的 FFmpeg 參數 (ultrafast, zerolatency, low_delay)。
- **雙路串流管理**: 同時管理遊戲畫面 (Game) 與攝影機畫面 (Cam)。
- **Web 管理介面**: 內建 Dashboard 可即時監控推流狀態、預覽畫面、管理參考圖片。
- **即時狀態偵測**: 針對遊戲畫面進行 Image Hash 比對，判斷 Normal / Select / Playing 狀態。
- **RESTful API**: 完整的狀態查詢與控制介面。

## 安裝流程

### 1. 系統依賴 (Ubuntu/Debian)

本專案依賴 systemd, ffmpeg 與 v4l2 utils。

```bash
sudo apt update
sudo apt install -y ffmpeg v4l-utils python3-pip python3-venv
```

請確保使用者有權限存取 `/dev/video*` 裝置：
```bash
sudo usermod -aG video $USER
```

### 2. 下載原始碼

```bash
cd /opt
sudo git clone https://github.com/lut333/egm-streamer.git egm-streamer
cd egm-streamer
sudo chown -R $USER:$USER .
```

### 3. 安裝 Python 模組

建議安裝於專屬的 venv 環境：

```bash
# 建立環境
python3 -m venv venv
source venv/bin/activate

# 安裝本模組
pip install .
```

## 更新版本

當服務有新版本發佈時，請依照以下步驟更新：

```bash
cd /opt/egm-streamer

# 1. 拉取最新程式碼
sudo git pull

# 2. 更新 Python 模組
source venv/bin/activate
pip install . --upgrade

# 3. 重啟服務
# 假設您的實例名稱為 egm-100
sudo systemctl restart egm-streamer@egm-100

# 如果是多台實例，可批量重啟
# sudo systemctl restart 'egm-streamer@*'
```

## 設定與使用

### 1. 準備設定檔

請參考 `config.example.yaml` 建立您的設定檔 (例如 `my_config.yaml`)。
IPC 版本通常包含 `game` 與 `cam` 兩路推流設定。

```yaml
common:
  instance_id: "egm-ipc-01"

streams:
  game:
    enabled: true
    input_device: "/dev/video0"    # 採集卡
    audio_device: "plughw:CARD=Video,DEV=0"
    rtmp_url: "rtmp://192.168.1.100:1935/game/101"
    ffmpeg_params:
      preset: "ultrafast"
      tune: "zerolatency"
      gop: 30
      
  cam:
    enabled: false # 若不使用可設為 false
    # input_device: "/dev/video2" 
    # rtmp_url: "rtmp://192.168.1.100:1935/game/101_cam"

detector:
  enabled: true
  target_stream: "game"  # 綁定 game stream 進行截圖分析
  
  # 狀態比對設定...
  states:
    NORMAL:
      refs_dir: "/var/lib/egm-streamer/refs/normal"
      # ...
```

### 2. 啟動服務

```bash
# 啟動並監聽 8080 port
egm-streamer serve --config my_config.yaml
```

成功啟動後，請打開瀏覽器訪問管理介面：
👉 **http://localhost:8080/**

在管理介面上，您可以：
- 查看各 Stream 的 FPS, Bitrate, Speed 與 Frames。
- 啟動/停止/重啟個別 Stream。
- 查看當前偵測到的遊戲狀態 (Normal/Select/Playing)。
- **一鍵採集參考圖**：點選 Tab 切換狀態，按下 "Capture Current as Reference" 即可將當前遊戲畫面存為該狀態的參考圖。

## API 說明

服務啟動後提供以下 API：

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/streams` | 取得所有 Stream 狀態 (Running, FPS, Bitrate) |
| POST | `/api/streams/{name}/control` | 控制 Stream (start/stop/restart) |
| GET | `/api/state` | 取得當前遊戲偵測狀態 |
| GET | `/api/refs/{state}` | 列出該狀態的所有參考圖 |
| POST | `/api/refs/{state}` | 將當前畫面新增為該狀態的參考圖 |
| DELETE| `/api/refs/{state}/{filename}` | 刪除參考圖 |

## 部署 (Systemd)

若要設為開機自動啟動，請使用 Systemd Template。

### 1. 安裝 Service File

```bash
cd /opt/egm-streamer
sudo cp egm-streamer@.service /etc/systemd/system/
sudo systemctl daemon-reload
```

這份 service 檔案預設使用 `/opt/egm-streamer/venv/bin/egm-streamer` 作為執行檔路徑。

### 2. 建立設定檔

設定檔需放在 `/etc/egm-streamer/`，檔名需對應您的 **實例名稱** (例如 `egm-100`)。

```bash
sudo mkdir -p /etc/egm-streamer
# 假設您目前的設定檔為 my_config.yaml
sudo cp my_config.yaml /etc/egm-streamer/egm-100.yaml
```

### 3. 啟動服務

啟動名稱為 `egm-100` 的實例：

```bash
# 設為開機自動啟動並立即執行
sudo systemctl enable --now egm-streamer@egm-100
```

### 4. 日常維運指令

- **查看狀態**：
  ```bash
  sudo systemctl status egm-streamer@egm-100
  ```
- **查看日誌 (即時)**：
  ```bash
  journalctl -u egm-streamer@egm-100 -f
  ```
- **重啟服務**：
  ```bash
  sudo systemctl restart egm-streamer@egm-100
  ```
- **停止服務**：
  ```bash
  sudo systemctl stop egm-streamer@egm-100
  ```
