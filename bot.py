import os, sqlite3, asyncio, io, csv
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import discord
from discord import app_commands
from discord.ext import commands, tasks

KST = ZoneInfo('Asia/Seoul')
TOKEN = os.getenv('DISCORD_TOKEN', '').strip()
if not TOKEN:
    raise RuntimeError('DISCORD_TOKEN 환경변수가 없습니다. Railway 서비스 Variables에 등록하고 배포하세요.')
DB_PATH = os.getenv('DB_PATH', 'activity.db')
GUILD_ID = int(os.getenv('GUILD_ID', '0'))
REPORT_CHANNEL_ID = int(os.getenv('REPORT_CHANNEL_ID', '0'))
STAFF_ROLE_ID = int(os.getenv('STAFF_ROLE_ID', '0'))
EXCLUDED_VOICE_IDS = {int(v) for v in os.getenv('EXCLUDED_VOICE_CHANNEL_IDS', '').split(',') if v.strip().isdigit()}
THRESHOLD = 7200
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.messages = True
bot = commands.Bot(command_prefix='!', intents=intents)
db_dir = os.path.dirname(os.path.abspath(DB_PATH))
try:
    os.makedirs(db_dir, exist_ok=True)
except OSError as exc:
    raise RuntimeError(f'DB 폴더 생성 실패: {db_dir}. Railway Volume을 /data에 연결하거나 DB_PATH=/tmp/activity.db로 테스트하세요.') from exc
try:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
except sqlite3.OperationalError as exc:
    raise RuntimeError(f'DB 열기 실패: {DB_PATH}. Volume 마운트와 쓰기 권한을 확인하세요.') from exc
conn.execute('PRAGMA journal_mode=WAL')
conn.executescript('''CREATE TABLE IF NOT EXISTS voice_sessions(guild_id INTEGER,user_id INTEGER,channel_id INTEGER,channel_name TEXT,start REAL,PRIMARY KEY(guild_id,user_id));
CREATE TABLE IF NOT EXISTS voice_segments(guild_id INTEGER,user_id INTEGER,channel_id INTEGER,channel_name TEXT,start REAL,end REAL);
CREATE TABLE IF NOT EXISTS message_events(guild_id INTEGER,user_id INTEGER,channel_id INTEGER,at REAL);
CREATE TABLE IF NOT EXISTS reports(guild_id INTEGER,week_start TEXT,PRIMARY KEY(guild_id,week_start));''')
conn.commit()

def now(): return datetime.now(timezone.utc).timestamp()
def monday(d):
    d=d.astimezone(KST)
    return (d-timedelta(days=d.weekday())).replace(hour=0,minute=0,second=0,microsecond=0)
def interval(weeks_ago=0):
    end=monday(datetime.now(KST))-timedelta(weeks=weeks_ago)
    return (end-timedelta(weeks=1)).timestamp(),end.timestamp()
def authorized(interaction):
    m=interaction.user
    return isinstance(m,discord.Member) and (m.guild_permissions.administrator or (STAFF_ROLE_ID and any(r.id==STAFF_ROLE_ID for r in m.roles)))
def fmt(seconds):
    minutes=int(seconds)//60
    return f'{minutes//60}시간 {minutes%60}분'
def voice_active(state):
    return bool(state and state.channel and not state.self_mute and not state.mute and not state.self_deaf and not state.deaf and state.channel.id not in EXCLUDED_VOICE_IDS)
def stop(gid,uid,t):
    row=conn.execute('SELECT channel_id,channel_name,start FROM voice_sessions WHERE guild_id=? AND user_id=?',(gid,uid)).fetchone()
    if row:
        cid,name,start=row
        if t>start: conn.execute('INSERT INTO voice_segments VALUES(?,?,?,?,?,?)',(gid,uid,cid,name,start,t))
        conn.execute('DELETE FROM voice_sessions WHERE guild_id=? AND user_id=?',(gid,uid))
def start(gid,uid,state,t):
    if voice_active(state): conn.execute('INSERT OR REPLACE INTO voice_sessions VALUES(?,?,?,?,?)',(gid,uid,state.channel.id,state.channel.name,t))
def sync_member(member,t):
    stop(member.guild.id,member.id,t)
    start(member.guild.id,member.id,member.voice,t)

def summary(guild,a,b):
    users={m.id:{'name':m.display_name,'voice':0.0,'messages':0,'rooms':{}} for m in guild.members if not m.bot}
    rows=conn.execute('SELECT user_id,channel_id,channel_name,start,end FROM voice_segments WHERE guild_id=? AND end>? AND start<?',(guild.id,a,b)).fetchall()
    rows+= [(uid,cid,name,st,now()) for uid,cid,name,st in conn.execute('SELECT user_id,channel_id,channel_name,start FROM voice_sessions WHERE guild_id=?',(guild.id,)).fetchall()]
    for uid,cid,name,st,en in rows:
        if uid not in users: continue
        seconds=max(0,min(en,b)-max(st,a))
        users[uid]['voice']+=seconds
        key=f'{name} ({cid})'
        users[uid]['rooms'][key]=users[uid]['rooms'].get(key,0)+seconds
    for uid,count in conn.execute('SELECT user_id,COUNT(*) FROM message_events WHERE guild_id=? AND at>=? AND at<? GROUP BY user_id',(guild.id,a,b)):
        if uid in users: users[uid]['messages']=count
    return users

def report_text(guild,a,b):
    users=summary(guild,a,b)
    inactive=sorted(((uid,v) for uid,v in users.items() if v['voice']<THRESHOLD),key=lambda x:x[1]['voice'])
    top=sorted(users.values(),key=lambda v:v['voice'],reverse=True)[:3]
    dates=f'{datetime.fromtimestamp(a,KST):%Y.%m.%d} ~ {(datetime.fromtimestamp(b,KST)-timedelta(seconds=1)):%Y.%m.%d}'
    lines=[f'📊 **주간 디스코드 활동 보고서** ({dates})',f'전체 {len(users)}명 | 활동 {len(users)-len(inactive)}명 | 미활동 {len(inactive)}명','**⚠️ 미활동 (2시간 미만)**']
    lines += [f'• {v["name"]}: {fmt(v["voice"])}' for _,v in inactive[:20]] or ['없음']
    if len(inactive)>20: lines.append(f'외 {len(inactive)-20}명 — /전체통계 또는 /통계내보내기 이용')
    lines.append('**🏆 음성 활동 TOP 3**')
    lines += [f'{i}. {v["name"]}: {fmt(v["voice"])}' for i,v in enumerate(top,1)]
    return '\n'.join(lines)[:1950]

@bot.event
async def on_ready():
    if not getattr(bot,'synced',False):
        if GUILD_ID:
            guild_obj=discord.Object(id=GUILD_ID)
            bot.tree.copy_global_to(guild=guild_obj)
            await bot.tree.sync(guild=guild_obj)
        else: await bot.tree.sync()
        bot.synced=True
    t=now()
    for guild in bot.guilds:
        # Recover live sessions after downtime; offline intervals are not recoverable.
        # 이전 세션을 재시작 시점까지 인정하지 않음: 다운타임은 계산할 수 없음.
        # 마지막 관측 시각까지 보존하려면 주기적 체크포인트가 필요함.
        conn.execute('DELETE FROM voice_sessions WHERE guild_id=?',(guild.id,))
        for channel in guild.voice_channels:
            for member in channel.members:
                if not member.bot: start(guild.id,member.id,member.voice,t)
    conn.commit()
    if not weekly_report.is_running(): weekly_report.start()
    print(f'Connected: {bot.user}')

@bot.event
async def on_voice_state_update(member,before,after):
    if member.bot: return
    t=now()
    stop(member.guild.id,member.id,t)
    start(member.guild.id,member.id,after,t)
    conn.commit()

@bot.event
async def on_message(message):
    if message.guild and not message.author.bot:
        conn.execute('INSERT INTO message_events VALUES(?,?,?,?)',(message.guild.id,message.author.id,message.channel.id,now()))
        conn.commit()
    await bot.process_commands(message)

@tasks.loop(minutes=1)
async def weekly_report():
    local=datetime.now(KST)
    if local.weekday()!=0 or local.hour!=0 or local.minute<5: return
    if not REPORT_CHANNEL_ID: return
    for guild in bot.guilds:
        if GUILD_ID and guild.id!=GUILD_ID: continue
        a,b=interval(0)
        week_start=datetime.fromtimestamp(a,KST).date().isoformat()
        if conn.execute('SELECT 1 FROM reports WHERE guild_id=? AND week_start=?',(guild.id,week_start)).fetchone(): continue
        channel=guild.get_channel(REPORT_CHANNEL_ID)
        if channel:
            try:
                await channel.send(report_text(guild,a,b))
                conn.execute('INSERT INTO reports VALUES(?,?)',(guild.id,week_start));conn.commit()
            except discord.HTTPException as e: print('Report failed:',e)

@weekly_report.before_loop
async def before_report(): await bot.wait_until_ready()

@bot.tree.command(name='전체통계',description='운영진 전용: 모든 인원의 주간 활동 통계')
@app_commands.describe(지난주='0=지난주 완료된 통계, 1=지지난주')
async def all_stats(interaction:discord.Interaction,지난주:app_commands.Range[int,0,52]=0):
    if not authorized(interaction): return await interaction.response.send_message('운영진만 조회할 수 있습니다.',ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    a,b=interval(지난주)
    users=summary(interaction.guild,a,b)
    ordered=sorted(users.values(),key=lambda v:v['voice'])
    lines=[f'{v["name"]} | 음성 {fmt(v["voice"])} | 채팅 {v["messages"]}개'+(' | 미활동' if v['voice']<THRESHOLD else '') for v in ordered]
    pages=['\n'.join(lines[i:i+15]) for i in range(0,len(lines),15)] or ['인원이 없습니다.']
    await interaction.followup.send(f'📊 전체 인원 {len(users)}명 / 미활동 {sum(v["voice"]<THRESHOLD for v in users.values())}명\n'+pages[0][:1700]+('\n전체 명단은 /통계내보내기 사용' if len(pages)>1 else ''),ephemeral=True)

@bot.tree.command(name='유저통계',description='운영진 전용: 유저의 통화방별 인정 시간')
async def user_stats(interaction:discord.Interaction,유저:discord.Member,지난주:app_commands.Range[int,0,52]=0):
    if not authorized(interaction): return await interaction.response.send_message('운영진만 조회할 수 있습니다.',ephemeral=True)
    a,b=interval(지난주); v=summary(interaction.guild,a,b).get(유저.id)
    if not v: return await interaction.response.send_message('해당 유저의 기록이 없습니다.',ephemeral=True)
    rooms=sorted(v['rooms'].items(),key=lambda x:x[1],reverse=True)
    msg=f'👤 {유저.display_name}\n음성 인정: {fmt(v["voice"])} / 채팅: {v["messages"]}개\n'+('\n'.join(f'🔊 {name}: {fmt(sec)}' for name,sec in rooms) or '통화방 기록 없음')
    await interaction.response.send_message(msg[:1900],ephemeral=True)

@bot.tree.command(name='미활동',description='운영진 전용: 주간 2시간 미만 인원')
async def inactive_stats(interaction:discord.Interaction,지난주:app_commands.Range[int,0,52]=0):
    if not authorized(interaction): return await interaction.response.send_message('운영진만 조회할 수 있습니다.',ephemeral=True)
    a,b=interval(지난주);users=summary(interaction.guild,a,b)
    inactive=sorted((v for v in users.values() if v['voice']<THRESHOLD),key=lambda v:v['voice'])
    lines=[f'{v["name"]}: {fmt(v["voice"])}' for v in inactive]
    msg=f'⚠️ 미활동 {len(inactive)}명 (2시간 미만)\n'+'\n'.join(lines[:45])
    if len(lines)>45: msg+='\n나머지는 /통계내보내기 확인'
    await interaction.response.send_message(msg[:1900],ephemeral=True)

@bot.tree.command(name='통계내보내기',description='운영진 전용: 전체 통계 CSV 다운로드')
async def export_stats(interaction:discord.Interaction,지난주:app_commands.Range[int,0,52]=0):
    if not authorized(interaction): return await interaction.response.send_message('운영진만 조회할 수 있습니다.',ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    a,b=interval(지난주);users=summary(interaction.guild,a,b)
    f=io.StringIO();w=csv.writer(f)
    w.writerow(['디스코드ID','닉네임','채팅수','음성인정초','음성인정시간','활동상태','통화방별 인정시간'])
    for uid,v in sorted(users.items(),key=lambda x:x[1]['voice']):
        w.writerow([uid,v['name'],v['messages'],int(v['voice']),fmt(v['voice']),'미활동' if v['voice']<THRESHOLD else '활동','; '.join(f'{name}: {fmt(sec)}' for name,sec in v['rooms'].items())])
    data=io.BytesIO(f.getvalue().encode('utf-8-sig'))
    await interaction.followup.send(file=discord.File(data,filename='weekly_activity.csv'),ephemeral=True)

@bot.tree.command(name='주간통계',description='운영진 전용: 지난주 보고서 수동 확인')
async def weekly_stats(interaction:discord.Interaction,지난주:app_commands.Range[int,0,52]=0):
    if not authorized(interaction): return await interaction.response.send_message('운영진만 조회할 수 있습니다.',ephemeral=True)
    a,b=interval(지난주)
    await interaction.response.send_message(report_text(interaction.guild,a,b),ephemeral=True)

bot.run(TOKEN)
