# 手機 ZIP 更新與部署

更新後台程式一次後，可用手機瀏覽器開啟 `/admin/zip-update`，選擇 ZIP、檢查差異，再按「提交更新並部署」。ZIP 不會在預覽時寫入 GitHub；確認後才會以單一提交更新部署分支。

## 伺服器設定

- `ADMIN_KEY`：必須設定為非空、非 `changeme` 的超級管理員密碼。上傳與提交 API 只接受此密碼，不接受一般管理員帳號。
- `GITHUB_TOKEN`：使用伺服器原有 GitHub Token，需對 `GITHUB_REPO` 有 Contents 讀取與寫入權限。Token 不會送到手機頁面。
- `GITHUB_REPO`：`owner/repository`，預設 `onerkk/line-translator-bot`。
- `GITHUB_DEPLOY_BRANCH`：提交目標分支，預設 `main`。
- `RENDER_DEPLOY_HOOK_URL`：選填。填入 Render Deploy Hook 可在提交後明確觸發部署；若未設定，依 Render 服務目前的 GitHub Auto-Deploy 設定部署。若 Auto-Deploy 已開啟，通常不需再設 Hook，以免觸發重複部署。

ZIP 限制為 139 MiB、最多 1,000 個檔案；解壓後最多 350 MiB，單一檔案最多 20 MiB，單次最多提交 250 個變更。支援完整專案包與只含變更檔的包；不會刪除 ZIP 未包含的檔案。會拒絕路徑穿越、符號連結、加密檔、Git 中繼資料與環境／私鑰檔。

完成更新時會比對新增、修改與相同檔案。預覽有效 15 分鐘；分支有新提交時必須重新檢查。部署狀態以 Render `RENDER_GIT_COMMIT` 環境值核對，因此需讓 Render 為服務提供該標準環境變數。
