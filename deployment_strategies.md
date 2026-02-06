# egm-streamer 專案分析與部屬方案建議

經過分析，您的專案 `egm-streamer` 是一個基於 Python (FastAPI) 與 FFmpeg 的即時推流與影像辨識應用。

## 1. 專案特性分析

*   **系統層依賴性高**: 高度依賴 `ffmpeg`、`v4l-utils` (video4linux) 以及硬體裝置 (`/dev/video*`, 音效卡)。
*   **硬體綁定**: 程式需要存取實體的影像擷取卡，這意味著無法單純在雲端或虛擬化環境運行，必須考慮與 Host OS 的驅動介接。
*   **Stateful (有狀態)**: 需要儲存 `refs/` (參考圖) 至本地磁碟，這意味著更新時不能隨意清空資料夾。
*   **多實例架構**: 目前設計透過 Systemd template (`@.service`) 來支援單機多實例 (Instance)，但在部屬與監控上需要對應的管理機制。

## 2. 部屬方案建議

針對「大量工控機 (IPC)」的場景，通常面臨網路受限、硬體驅動差異、以及需要遠端統一更新的挑戰。以下提出三種建議方案，按推薦程度排序：

### 方案 A：容器化部屬 (Docker + Docker Compose) - **[推薦]**

將應用程式與其依賴 (FFmpeg, Python env) 打包在一起，解決「依賴地獄」問題。

*   **優點**:
    *   **環境一致性**: 確保每一台 IPC 跑的 Python 與 FFmpeg 版本完全一樣，不受 Host OS 更新影響。
    *   **更新容易**: 更新只需 `docker pull` + `docker-compose up -d`。
    *   **隔離性**: 不會汙染 Host OS 的環境。
*   **挑戰**:
    *   需要處理硬體權限映射 (`--device /dev/video0`)。
*   **實作建議**:
    1.  撰寫 `Dockerfile`，以 `python:3.9-slim` 為底，安裝 `ffmpeg`。
    2.  撰寫 `docker-compose.yml`，使用 `volumes` 持久化 `refs/` 目錄，並掛載 `/dev/video*`。

    ```dockerfile
    # Dockerfile 範例
    FROM python:3.9-slim
    
    # 安裝系統依賴
    RUN apt-get update && apt-get install -y ffmpeg v4l-utils && rm -rf /var/lib/apt/lists/*
    
    WORKDIR /app
    COPY . .
    RUN pip install .
    
    ENTRYPOINT ["egm-streamer", "serve"]
    ```

    ```yaml
    # docker-compose.yml 範例
    services:
      egm-streamer:
        image: your-registry/egm-streamer:latest
        restart: always
        devices:
          - "/dev/video0:/dev/video0"
          - "/dev/snd:/dev/snd"
        volumes:
          - "./refs:/var/lib/egm-streamer/refs"
          - "./config.yaml:/app/config.yaml"
        command: ["--config", "/app/config.yaml"]
        network_mode: "host" 
    ```

### 方案 B：自動化組態管理 (Ansible)

如果您希望維持目前的「裸機 (Bare Metal)」執行方式，或者 IPC 硬體資源極其受限不適合跑 Docker，則推薦使用 Ansible。

*   **優點**:
    *   不需要安裝 Docker Daemon。
    *   可以直接利用您現有的 Systemd 架構。
*   **缺點**:
    *   OS 升級可能破壞依賴 (例如 Python 版本改變)。
    *   初次設定較為繁瑣。
*   **實作建議**:
    *   編寫 Ansible Playbook 處理 apt install, git clone, pip install, systemd restart。

### 方案 C：打包為系統套件 (.deb)

*   **優點**: 極致標準化：`apt install egm-streamer`。
*   **缺點**: 維護成本高。

## 3. 綜合建議

**首選策略**：採用 **方案 A (Docker)**。
理由是工控機通常部署後不易頻繁手動介入，容器化能最大程度保證穩定性。

**下一步行動建議**:
1.  **建立 Dockerfile**: 將專案容器化。
2.  **建立 CI/CD 流程**: 自動 Build Docker Image。
