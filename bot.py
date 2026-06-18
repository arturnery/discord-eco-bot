import asyncio
import discord
from discord.ext import commands, tasks
import logging
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

from scraper import ForexFactoryScraper
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
scraper = ForexFactoryScraper()

TZ_BRT = ZoneInfo("America/Sao_Paulo")
ESTADO_FILE = Path("estado.txt")

EMOJIS_IMPACTO = {"alto": "🔴", "medio": "🟠", "baixo": "🟡"}
EMOJIS_PAIS = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵",
    "CAD": "🇨🇦", "AUD": "🇦🇺", "NZD": "🇳🇿", "CHF": "🇨🇭",
    "BRL": "🇧🇷", "CNY": "🇨🇳",
}
NIVEL_IMPACTO = {"alto": 2, "medio": 1, "baixo": 0}

# Eventos/moedas que recebem card completo — os que movem mercado global
MOEDAS_TOP_TIER = {"USD", "EUR", "GBP"}
PALAVRAS_TOP_TIER = {
    "federal funds", "non-farm", "nonfarm", "nfp", "cpi", "gdp",
    "refinancing rate", "bank rate", "boj policy", "unemployment rate",
    "advance gdp", "core pce", "pce", "fomc",
}

# Cache do scraper
_cache_eventos: list[dict] = []
_cache_ts: datetime | None = None
CACHE_TTL_SEGUNDOS = 120

# ── Análise de impacto para crypto/mercado americano ─────────────────────────
# Formato: "palavra-chave" -> (sentido, msg_acima, msg_abaixo)
# sentido: "normal" = acima é ruim para crypto | "inverso" = abaixo é ruim
_ANALISE: dict[str, tuple[str, str, str]] = {
    # Emprego
    "non-farm":        ("normal",  "Emprego forte → Fed hawkish 🐻 bearish",  "Emprego fraco → Fed dovish 🐂 bullish"),
    "nonfarm":         ("normal",  "Emprego forte → Fed hawkish 🐻 bearish",  "Emprego fraco → Fed dovish 🐂 bullish"),
    "nfp":             ("normal",  "Emprego forte → Fed hawkish 🐻 bearish",  "Emprego fraco → Fed dovish 🐂 bullish"),
    "employment change":("normal", "Emprego forte → Fed hawkish 🐻 bearish",  "Emprego fraco → Fed dovish 🐂 bullish"),
    "unemployment":    ("inverso", "Desemprego cai → Fed hawkish 🐻 bearish", "Desemprego sobe → Fed dovish 🐂 bullish"),
    "jobless claims":  ("inverso", "Menos pedidos → Fed hawkish 🐻 bearish",  "Mais pedidos → Fed dovish 🐂 bullish"),
    # Inflação
    "cpi":             ("normal",  "Inflação alta → Fed hawkish 🐻 bearish",  "Inflação cede → Fed dovish 🐂 bullish"),
    "core cpi":        ("normal",  "Inflação alta → Fed hawkish 🐻 bearish",  "Inflação cede → Fed dovish 🐂 bullish"),
    "pce":             ("normal",  "Inflação alta → Fed hawkish 🐻 bearish",  "Inflação cede → Fed dovish 🐂 bullish"),
    "core pce":        ("normal",  "Inflação alta → Fed hawkish 🐻 bearish",  "Inflação cede → Fed dovish 🐂 bullish"),
    "ppi":             ("normal",  "Inflação alta → Fed hawkish 🐻 bearish",  "Inflação cede → Fed dovish 🐂 bullish"),
    # Crescimento
    "gdp":             ("normal",  "Crescimento forte → Fed hawkish 🐻 bearish", "Crescimento fraco → Fed dovish 🐂 bullish"),
    "advance gdp":     ("normal",  "Crescimento forte → Fed hawkish 🐻 bearish", "Crescimento fraco → Fed dovish 🐂 bullish"),
    # Consumo
    "retail sales":    ("normal",  "Consumo forte → Fed hawkish 🐻 bearish",  "Consumo fraco → Fed dovish 🐂 bullish"),
    "consumer confidence":("normal","Confiança alta → Fed hawkish 🐻 bearish","Confiança baixa → Fed dovish 🐂 bullish"),
    "consumer sentiment":("normal","Confiança alta → Fed hawkish 🐻 bearish", "Confiança baixa → Fed dovish 🐂 bullish"),
    # Atividade
    "ism manufacturing":("normal", "Indústria forte → Fed hawkish 🐻 bearish", "Indústria fraca → Fed dovish 🐂 bullish"),
    "ism services":    ("normal",  "Serviços fortes → Fed hawkish 🐻 bearish", "Serviços fracos → Fed dovish 🐂 bullish"),
    "manufacturing pmi":("normal", "Indústria forte → Fed hawkish 🐻 bearish", "Indústria fraca → Fed dovish 🐂 bullish"),
    "services pmi":    ("normal",  "Serviços fortes → Fed hawkish 🐻 bearish", "Serviços fracos → Fed dovish 🐂 bullish"),
    "factory orders":  ("normal",  "Indústria forte → Fed hawkish 🐻 bearish", "Indústria fraca → Fed dovish 🐂 bullish"),
    # Habitação
    "housing starts":  ("normal",  "Construção forte → Fed hawkish 🐻 bearish","Construção fraca → Fed dovish 🐂 bullish"),
    "building permits":("normal",  "Construção forte → Fed hawkish 🐻 bearish","Construção fraca → Fed dovish 🐂 bullish"),
    # Fed
    "federal funds":   (None, "🏛️ Decisão do Fed — acompanhe a declaração", "🏛️ Decisão do Fed — acompanhe a declaração"),
    "fomc":            (None, "🏛️ Declaração do Fed — mercado em alerta",    "🏛️ Declaração do Fed — mercado em alerta"),
}


def analisar_impacto_crypto(evento: dict, surpresa_sentido: str | None) -> str | None:
    """Retorna análise de impacto para crypto com base no indicador e resultado."""
    titulo = evento.get("titulo", "").lower()
    match = next((v for k, v in _ANALISE.items() if k in titulo), None)
    if not match:
        return None

    sentido, msg_acima, msg_abaixo = match

    if sentido is None:
        return msg_acima

    if surpresa_sentido is None:
        return None

    acima = surpresa_sentido == "acima"
    if sentido == "normal":
        return msg_acima if acima else msg_abaixo
    else:  # inverso
        return msg_abaixo if acima else msg_acima

# Controle persistido
alertas_enviados: set[str] = set()
resultados_enviados: set[str] = set()


# ── Helpers — valores e surpresa ──────────────────────────────────────────────

def _parse_valor(s: str) -> float | None:
    if not s:
        return None
    s = s.strip().replace(",", "").replace(" ", "").rstrip("%")
    if not s:
        return None
    sufixos = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "T": 1_000_000_000_000}
    if s[-1].upper() in sufixos:
        try:
            return float(s[:-1]) * sufixos[s[-1].upper()]
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def calcular_surpresa(real_str: str, prev_str: str) -> tuple[str, str, str | None] | None:
    """Retorna (emoji, descricao, sentido) onde sentido é 'acima', 'abaixo' ou None (em linha)."""
    real = _parse_valor(real_str)
    prev = _parse_valor(prev_str)
    if real is None or prev is None:
        return None

    diff = real - prev
    abs_diff = abs(diff)
    threshold_linha = max(0.05, abs(prev) * 0.05) if prev != 0 else 0.05
    threshold_grande = max(0.30, abs(prev) * 0.15) if prev != 0 else 0.30

    if abs_diff <= threshold_linha:
        return ("⚪", "em linha", None)
    elif diff > 0:
        return ("🟢🟢", f"↑↑ surpresa (+{abs_diff:.2f})", "acima") if abs_diff >= threshold_grande else ("🟢", f"↑ acima (+{abs_diff:.2f})", "acima")
    else:
        return ("🔴🔴", f"↓↓ surpresa ({diff:.2f})", "abaixo") if abs_diff >= threshold_grande else ("🔴", f"↓ abaixo ({diff:.2f})", "abaixo")


def historico_vs_anterior(real_str: str, ant_str: str) -> str | None:
    real = _parse_valor(real_str)
    ant = _parse_valor(ant_str)
    if real is None or ant is None:
        return None
    diff = real - ant
    if abs(diff) < 0.01:
        return "= estável vs anterior"
    return f"↑ acelerou vs anterior (+{diff:.2f})" if diff > 0 else f"↓ desacelerou vs anterior ({diff:.2f})"


def is_top_tier(evento: dict) -> bool:
    moeda = evento.get("moeda", "")
    titulo = evento.get("titulo", "").lower()
    return moeda in MOEDAS_TOP_TIER and any(p in titulo for p in PALAVRAS_TOP_TIER)


def cor_embed(evento: dict) -> discord.Color:
    impacto = evento.get("impacto", "baixo")
    if impacto == "alto" and is_top_tier(evento):
        return discord.Color.red()
    if impacto == "alto":
        return discord.Color.orange()
    if impacto == "medio":
        return discord.Color.yellow()
    return discord.Color.light_grey()


def discord_ts(horario_dt, fmt: str = "t") -> str:
    if not horario_dt:
        return "—"
    return f"<t:{int(horario_dt.timestamp())}:{fmt}>"


# ── Helpers — estado.txt ──────────────────────────────────────────────────────

def _carregar_estado():
    if not ESTADO_FILE.exists():
        return
    linhas = ESTADO_FILE.read_text().splitlines()
    if not linhas or linhas[0] != str(date.today()):
        return
    for linha in linhas[1:]:
        if linha.startswith("resultado:"):
            resultados_enviados.add(linha[len("resultado:"):])
        elif linha.startswith("alerta:"):
            alertas_enviados.add(linha[len("alerta:"):])


def _salvar_estado():
    linhas = [str(date.today())]
    if _agenda_marcada:
        linhas.append("agenda")
    linhas += [f"resultado:{r}" for r in sorted(resultados_enviados)]
    linhas += [f"alerta:{a}" for a in sorted(alertas_enviados)]
    ESTADO_FILE.write_text("\n".join(linhas))


_agenda_marcada: bool = False


def agenda_postada_hoje() -> bool:
    return _agenda_marcada


def marcar_agenda_postada():
    global _agenda_marcada
    _agenda_marcada = True
    _salvar_estado()


def marcar_resultado_enviado(evento_id: str):
    resultados_enviados.add(evento_id)
    _salvar_estado()


def marcar_alerta_enviado(evento_id: str):
    alertas_enviados.add(evento_id)
    _salvar_estado()


def filtrar_por_impacto(eventos: list[dict], minimo: str) -> list[dict]:
    nivel_min = NIVEL_IMPACTO.get(minimo, 0)
    return [
        e for e in eventos
        if NIVEL_IMPACTO.get(e["impacto"], 0) >= nivel_min
        and not (e.get("horario") == "All Day" and e.get("impacto") != "alto")
        and not (is_discurso(e) and e.get("impacto") != "alto")
        and (not Config.MOEDAS_FILTRO or e.get("moeda") in Config.MOEDAS_FILTRO)
    ]


def obter_eventos_dia() -> list[dict]:
    global _cache_eventos, _cache_ts
    agora = datetime.now(TZ_BRT)
    if _cache_ts and (agora - _cache_ts).total_seconds() < CACHE_TTL_SEGUNDOS:
        return _cache_eventos
    _cache_eventos = scraper.buscar_eventos_dia()
    _cache_ts = agora
    return _cache_eventos


# ── Embeds ────────────────────────────────────────────────────────────────────

def is_discurso(evento: dict) -> bool:
    return not evento.get("anterior") and not evento.get("previsao") and not evento.get("real")


def embed_agenda(eventos: list[dict]) -> discord.Embed:
    hoje = datetime.now(TZ_BRT).strftime("%d/%m/%Y")
    embed = discord.Embed(
        title=f"📅 Agenda Econômica — {hoje}",
        color=discord.Color.blurple(),
    )
    for i, ev in enumerate(eventos[:12]):
        impacto = ev.get("impacto", "baixo")
        moeda = ev.get("moeda", "?")
        horario_dt = ev.get("horario_dt")
        real = ev.get("real", "")
        horario_fmt = discord_ts(horario_dt, "t") if horario_dt else ev.get("horario", "—")
        titulo = ev.get("titulo", "?")

        field_name = f"{EMOJIS_IMPACTO.get(impacto)} {horario_fmt} — {EMOJIS_PAIS.get(moeda, '🌐')} {moeda} — {titulo}"

        if is_discurso(ev):
            embed.add_field(name=field_name, value="💬 Discurso / Evento sem dados", inline=False)
        else:
            ant = ev.get("anterior") or "—"
            prev = ev.get("previsao") or "—"
            surpresa_obj = calcular_surpresa(real, prev) if real else None
            surpresa_str = f" {surpresa_obj[0]} {surpresa_obj[1]}" if surpresa_obj else ""
            analise = analisar_impacto_crypto(ev, surpresa_obj[2] if surpresa_obj else None) if real else None
            analise_str = f"\n₿ {analise}" if analise else ""

            if real:
                valor = f"📌 Ant: `{ant}`  |  🔮 Prev: `{prev}`  |  ✅ **`{real}`**{surpresa_str}{analise_str}"
            else:
                valor = f"📌 Ant: `{ant}`  |  🔮 Prev: `{prev}`"

            embed.add_field(name=field_name, value=valor, inline=False)

        if i < len(eventos[:12]) - 1:
            embed.add_field(name="​", value="─────────────────────", inline=False)

    embed.set_footer(text="ForexFactory • EcoBot")
    return embed


def embed_alerta(evento: dict) -> discord.Embed:
    moeda = evento.get("moeda", "?")
    impacto = evento.get("impacto", "alto")
    horario_dt = evento.get("horario_dt")
    titulo = evento.get("titulo", "Sem título")
    ant = evento.get("anterior") or "—"
    prev = evento.get("previsao") or "—"

    horario_str = (
        f"{discord_ts(horario_dt, 't')} ({discord_ts(horario_dt, 'R')})"
        if horario_dt else evento.get("horario", "—")
    )

    field_name = f"🚨 Em {Config.MINUTOS_ALERTA}min — {EMOJIS_IMPACTO.get(impacto)} {horario_str} — {EMOJIS_PAIS.get(moeda, '🌐')} {moeda} — {titulo}"

    embed = discord.Embed(
        title=f"🚨 Agenda Econômica — Aviso",
        color=cor_embed(evento),
    )
    embed.add_field(
        name=field_name,
        value=f"📌 Ant: `{ant}`  |  🔮 Prev: `{prev}`",
        inline=False,
    )
    embed.set_footer(text="ForexFactory • EcoBot")
    return embed


def embed_resultado(evento: dict) -> discord.Embed:
    moeda = evento.get("moeda", "?")
    real = evento.get("real", "—")
    prev = evento.get("previsao") or "—"
    ant = evento.get("anterior") or "—"
    horario_dt = evento.get("horario_dt")
    impacto = evento.get("impacto", "alto")
    titulo = evento.get("titulo", "Sem título")
    surpresa = calcular_surpresa(real, prev)
    surpresa_str = f" {surpresa[0]} {surpresa[1]}" if surpresa else ""
    analise = analisar_impacto_crypto(evento, surpresa[2] if surpresa else None)
    analise_str = f"\n₿ {analise}" if analise else ""

    horario_str = (
        f"{discord_ts(horario_dt, 't')} ({discord_ts(horario_dt, 'R')})"
        if horario_dt else evento.get("horario", "—")
    )

    field_name = f"📊 {EMOJIS_IMPACTO.get(impacto)} {horario_str} — {EMOJIS_PAIS.get(moeda, '🌐')} {moeda} — {titulo}"

    embed = discord.Embed(title="📊 Resultado", color=cor_embed(evento))
    embed.add_field(
        name=field_name,
        value=f"📌 Ant: `{ant}`  |  🔮 Prev: `{prev}`  |  ✅ **`{real}`**{surpresa_str}{analise_str}",
        inline=False,
    )
    embed.set_footer(text="ForexFactory • EcoBot")
    return embed


def embed_resultado_agrupado(eventos: list[dict]) -> discord.Embed:
    ev0 = eventos[0]
    moeda = ev0.get("moeda", "?")
    horario_dt = ev0.get("horario_dt")
    impacto = ev0.get("impacto", "alto")

    horario_str = (
        f"{discord_ts(horario_dt, 't')} ({discord_ts(horario_dt, 'R')})"
        if horario_dt else ev0.get("horario", "—")
    )

    embed = discord.Embed(title="📊 Resultado", color=cor_embed(ev0))

    for ev in eventos:
        real = ev.get("real", "—")
        prev = ev.get("previsao") or "—"
        ant = ev.get("anterior") or "—"
        titulo = ev.get("titulo", "?")
        surpresa = calcular_surpresa(real, prev)
        surpresa_str = f" {surpresa[0]} {surpresa[1]}" if surpresa else ""
        analise = analisar_impacto_crypto(ev, surpresa[2] if surpresa else None)
        analise_str = f"\n₿ {analise}" if analise else ""

        field_name = f"📊 {EMOJIS_IMPACTO.get(impacto)} {horario_str} — {EMOJIS_PAIS.get(moeda, '🌐')} {moeda} — {titulo}"
        embed.add_field(
            name=field_name,
            value=f"📌 Ant: `{ant}`  |  🔮 Prev: `{prev}`  |  ✅ **`{real}`**{surpresa_str}{analise_str}",
            inline=False,
        )

    embed.set_footer(text="ForexFactory • EcoBot")
    return embed


# ── Jobs automáticos ──────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    global _agenda_marcada
    logger.info(f"Bot conectado como {bot.user} (ID: {bot.user.id})")
    _carregar_estado()
    if ESTADO_FILE.exists():
        linhas = ESTADO_FILE.read_text().splitlines()
        if linhas and linhas[0] == str(date.today()) and "agenda" in linhas:
            _agenda_marcada = True
    logger.info(
        f"Estado carregado — resultados: {len(resultados_enviados)}, "
        f"alertas: {len(alertas_enviados)}, agenda hoje: {_agenda_marcada}"
    )
    job_agenda_diaria.start()
    job_monitor.start()
    job_reset_meia_noite.start()
    logger.info("Jobs iniciados.")


@tasks.loop(minutes=1)
async def job_agenda_diaria():
    agora = datetime.now(TZ_BRT)
    h, m = Config.HORARIO_AGENDA.split(":")
    if agora.hour != int(h) or agora.minute != int(m):
        return
    if agenda_postada_hoje():
        return

    canal = bot.get_channel(Config.CANAL_ID)
    if not canal:
        logger.error(f"Canal {Config.CANAL_ID} não encontrado.")
        return

    eventos = obter_eventos_dia()
    filtrados = filtrar_por_impacto(eventos, Config.IMPACTO_MINIMO_AGENDA)

    if not filtrados:
        await canal.send("📭 Nenhum evento relevante encontrado para hoje.")
    else:
        await canal.send(content="@everyone", embed=embed_agenda(filtrados), allowed_mentions=discord.AllowedMentions(everyone=True))

    marcar_agenda_postada()
    logger.info("Agenda diária postada.")


async def _verificar_resultados_e_alertas(canal):
    """Verifica resultados e alertas para eventos de alto impacto."""
    eventos = obter_eventos_dia()
    filtrados = filtrar_por_impacto(eventos, "alto")
    agora = datetime.now(TZ_BRT)

    # ── Resultados ────────────────────────────────────────────────────────────
    pendentes = [
        ev for ev in filtrados
        if ev.get("real") and f"{ev['titulo']}_{ev['horario']}" not in resultados_enviados
    ]
    grupos: dict[tuple, list] = {}
    for ev in pendentes:
        grupos.setdefault((ev["moeda"], ev["horario"]), []).append(ev)

    for evs in grupos.values():
        if len(evs) > 1:
            await canal.send(content="@everyone", embed=embed_resultado_agrupado(evs), allowed_mentions=discord.AllowedMentions(everyone=True))
        else:
            await canal.send(content="@everyone", embed=embed_resultado(evs[0]), allowed_mentions=discord.AllowedMentions(everyone=True))
        for ev in evs:
            evento_id = f"{ev['titulo']}_{ev['horario']}"
            marcar_resultado_enviado(evento_id)
            logger.info(f"Resultado postado: {ev['titulo']}")

    # ── Alertas pré-evento ────────────────────────────────────────────────────
    for evento in filtrados:
        evento_id = f"{evento['titulo']}_{evento['horario']}"
        if evento_id in alertas_enviados:
            continue
        horario_dt = evento.get("horario_dt")
        if not horario_dt:
            continue
        diferenca = (horario_dt - agora).total_seconds() / 60
        margem = Config.MINUTOS_ALERTA
        if margem - 2 <= diferenca <= margem + 2:
            await canal.send(content="@everyone", embed=embed_alerta(evento), allowed_mentions=discord.AllowedMentions(everyone=True))
            marcar_alerta_enviado(evento_id)
            logger.info(f"Alerta pré-evento postado: {evento['titulo']}")


def _proximo_evento_alto(agora: datetime) -> dict | None:
    """Retorna o próximo evento de alto impacto sem resultado e com horário definido."""
    eventos = obter_eventos_dia()
    candidatos = [
        ev for ev in eventos
        if ev.get("impacto") == "alto"
        and not is_discurso(ev)
        and ev.get("horario_dt")
        and f"{ev['titulo']}_{ev['horario']}" not in resultados_enviados
        and (ev["horario_dt"] - agora).total_seconds() > -1800  # até 30min depois
    ]
    candidatos.sort(key=lambda e: e["horario_dt"])
    return candidatos[0] if candidatos else None


@tasks.loop(seconds=1)
async def job_monitor():
    canal = bot.get_channel(Config.CANAL_ID)
    if not canal:
        await asyncio.sleep(60)
        return

    agora = datetime.now(TZ_BRT)
    proximo = _proximo_evento_alto(agora)

    if not proximo:
        logger.info("Nenhum evento alto impacto pendente. Monitor dorme 1h.")
        await asyncio.sleep(3600)
        return

    horario_dt = proximo["horario_dt"]
    diff_seg = (horario_dt - agora).total_seconds()

    if diff_seg > (Config.MINUTOS_ALERTA + 3) * 60:
        # Dorme até Config.MINUTOS_ALERTA + 3min antes do evento
        sleep = diff_seg - (Config.MINUTOS_ALERTA + 3) * 60
        logger.info(f"Próximo evento: {proximo['titulo']} em {diff_seg/60:.0f}min. Dormindo {sleep/60:.0f}min.")
        await asyncio.sleep(sleep)
        return

    # Janela ativa — verifica resultados e alertas
    await _verificar_resultados_e_alertas(canal)
    await asyncio.sleep(30)


@tasks.loop(minutes=1)
async def job_reset_meia_noite():
    global _agenda_marcada
    agora = datetime.now(TZ_BRT)
    if agora.hour == 0 and agora.minute == 0:
        alertas_enviados.clear()
        resultados_enviados.clear()
        _agenda_marcada = False
        if ESTADO_FILE.exists():
            ESTADO_FILE.write_text("{}")
        logger.info("Estado resetado à meia-noite.")


@job_agenda_diaria.before_loop
@job_monitor.before_loop
@job_reset_meia_noite.before_loop
async def before_jobs():
    await bot.wait_until_ready()


# ── Comandos manuais ──────────────────────────────────────────────────────────

@bot.command(name="agenda")
async def cmd_agenda(ctx):
    """!agenda — mostra todos os eventos do dia."""
    eventos = scraper.buscar_eventos_dia()
    filtrados = filtrar_por_impacto(eventos, "baixo")
    if not filtrados:
        await ctx.send("📭 Nenhum evento encontrado para hoje.")
        return
    await ctx.send(content="@everyone", embed=embed_agenda(filtrados), allowed_mentions=discord.AllowedMentions(everyone=True))


@bot.command(name="semana")
async def cmd_semana(ctx):
    """!semana — lista eventos dos próximos 7 dias."""
    await ctx.send("🔍 Buscando eventos da semana, aguarde...")
    eventos = scraper.buscar_eventos_semana()
    if not eventos:
        await ctx.send("📭 Nenhum evento encontrado para os próximos 7 dias.")
        return

    por_dia: dict = {}
    for ev in eventos:
        por_dia.setdefault(ev["data"], []).append(ev)

    dias_pt = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
    meses_pt = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

    for dia, evs in sorted(por_dia.items()):
        nome_dia = f"{dias_pt[dia.weekday()]}, {dia.day} {meses_pt[dia.month - 1]}"
        embed = discord.Embed(
            title=f"📅 {nome_dia} — Eventos",
            color=discord.Color.blurple(),
        )
        for ev in evs[:15]:
            impacto = ev.get("impacto", "baixo")
            moeda = ev.get("moeda", "?")
            horario_dt = ev.get("horario_dt")
            real = ev.get("real", "")
            real_str = f" | ✅ **{real}**" if real else ""
            horario_fmt = discord_ts(horario_dt, "t") if horario_dt else ev.get("horario", "—")
            embed.add_field(
                name=f"{EMOJIS_IMPACTO.get(impacto)} {horario_fmt} — {EMOJIS_PAIS.get(moeda, '🌐')} {moeda}",
                value=(
                    f"{ev.get('titulo', '?')}\n"
                    f"📌 Ant: `{ev.get('anterior') or '—'}` | "
                    f"🔮 Prev: `{ev.get('previsao') or '—'}`{real_str}"
                ),
                inline=False,
            )
        embed.set_footer(text="ForexFactory • EcoBot")
        await ctx.send(content="@everyone", embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))


@bot.command(name="legenda")
async def cmd_legenda(ctx):
    """!legenda — explica os ícones e a lógica da agenda."""
    embed = discord.Embed(
        title="📖 Legenda — Como ler a Agenda",
        color=discord.Color.blurple(),
    )

    embed.add_field(
        name="💥 Impacto do Evento",
        value=(
            "🔴 Alto — pode mover o mercado\n"
            "🟠 Médio — atenção moderada\n"
            "🟡 Baixo — pouco impacto esperado"
        ),
        inline=False,
    )

    embed.add_field(
        name="📊 Resultado vs Previsão",
        value=(
            "🟢🟢 Surpresa grande positiva — veio muito acima do esperado\n"
            "🟢 Acima do esperado\n"
            "⚪ Em linha com a previsão\n"
            "🔴 Abaixo do esperado\n"
            "🔴🔴 Surpresa grande negativa — veio muito abaixo do esperado\n"
            "📊 Resultado sem previsão disponível"
        ),
        inline=False,
    )

    embed.add_field(
        name="📌 Campos dos Eventos",
        value=(
            "📌 Ant — valor do mês/período anterior\n"
            "🔮 Prev — previsão do mercado\n"
            "O resultado aparece junto à previsão com o ícone de surpresa (🟢/🔴/⚪)"
        ),
        inline=False,
    )

    embed.add_field(
        name="₿ Impacto Crypto/Mercado Americano",
        value=(
            "Para investidores em Bitcoin, crypto e mercado americano, "
            "o que mais importa é o impacto nos juros do Fed:\n\n"
            "🐻 **Bearish** — dado forte afasta corte de juros → pressão vendedora\n"
            "🐂 **Bullish** — dado fraco abre espaço para corte de juros → pressão compradora\n\n"
            "_Exemplo: NFP alto = economia forte = Fed mantém juros = bearish crypto_"
        ),
        inline=False,
    )

    embed.add_field(
        name="💬 Discursos",
        value="Eventos sem dados numéricos (reuniões, falas de autoridades). Apenas impacto alto é exibido.",
        inline=False,
    )

    embed.set_footer(text="ForexFactory • EcoBot")
    await ctx.send(embed=embed)


@bot.command(name="status")
async def cmd_status(ctx):
    """!status — mostra o estado atual do bot."""
    embed = discord.Embed(title="✅ EcoBot Online", color=discord.Color.green())
    embed.add_field(name="Canal monitorado", value=f"<#{Config.CANAL_ID}>", inline=False)
    embed.add_field(name="Agenda diária", value=f"{Config.HORARIO_AGENDA} BRT", inline=True)
    embed.add_field(name="Alerta pré-evento", value=f"{Config.MINUTOS_ALERTA}min antes", inline=True)
    embed.add_field(name="Impacto agenda", value=Config.IMPACTO_MINIMO_AGENDA, inline=True)
    embed.add_field(name="Impacto alertas", value=Config.IMPACTO_MINIMO_ALERTA, inline=True)
    embed.add_field(name="Alertas enviados", value=str(len(alertas_enviados)), inline=True)
    embed.add_field(name="Resultados enviados", value=str(len(resultados_enviados)), inline=True)
    embed.add_field(name="Agenda postada hoje", value="✅ Sim" if agenda_postada_hoje() else "❌ Não", inline=True)
    embed.set_footer(text="ForexFactory • EcoBot")
    await ctx.send(embed=embed)


if __name__ == "__main__":
    Config.validar()
    bot.run(Config.TOKEN)
