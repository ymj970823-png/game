# 디스코드 활동 통계 봇

## 기능
- 주간 집계: 월요일 00:00 ~ 다음 월요일 00:00 (한국 시간)
- 마이크가 켜진 음성 채널 체류 시간만 인정 (혼자 있어도 인정)
- 본인/서버 음소거 및 헤드셋 음소거 시간 제외
- 통화방 이름별 인정 시간, 메시지 수 기록
- 2시간 미만 미활동 판정, 활동 없는 서버 구성원도 포함
- 월요일 00:05 지난주 보고서 자동 게시 (설정 채널)
- 운영진 전용 `/전체통계`, `/유저통계`, `/미활동`, `/통계내보내기`, `/주간통계`
- 특정 음성 채널 제외는 환경변수 EXCLUDED_VOICE_CHANNEL_IDS에 쉼표로 ID 기재

## 설치
1. Python 3.11 이상 준비
2. `pip install -r requirements.txt`
3. Discord Developer Portal에서 Bot 생성 후 Token 발급
4. Bot 설정에서 **SERVER MEMBERS INTENT** 활성화. 초대 시 bot + applications.commands 범위 및 View Channels, Send Messages, Attach Files 권한 부여
5. `.env.example` 값을 참고해 환경변수 설정 (`.env` 파일을 자동으로 읽지는 않음)
6. `python bot.py`

### Railway
- GitHub에 파일 업로드 후 Railway 프로젝트 연결
- 시작 명령: `python bot.py`
- 환경변수 등록: DISCORD_TOKEN, GUILD_ID, REPORT_CHANNEL_ID, STAFF_ROLE_ID, DB_PATH=/data/activity.db
- Railway Volume을 `/data`에 마운트 (재배포 후 기록 유지)

## 참고 사항
- 설치 이후 기록만 집계합니다. 이전 음성 접속 기록은 복구할 수 없습니다.
- 재시작/오프라인 동안의 실제 음성 상태 변동은 추적할 수 없으므로 해당 기간의 정확한 집계는 불가능합니다.
- 봇 재시작 시 진행 중인 음성 세션은 재접속 시점부터 다시 측정합니다.
- 음소거 상태만 측정하며 실제 발언 여부는 측정하지 않습니다.
- 통화방 이름이 바뀌면 변경 시점 이후의 세션부터 새 이름으로 기록되며 채널 ID는 함께 저장됩니다.
- 서버 멤버 목록을 불러오기 위해 Members Intent가 필요합니다.
- 자동 보고는 월요일 00:05에 봇이 실행 중이어야 발송됩니다.
- 주간 보고 채널은 운영진만 볼 수 있도록 Discord 채널 권한을 설정하세요.
- 시간은 한국 표준시 기준입니다.
