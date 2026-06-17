import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Obrigatório ───────────────────────────────────────────────────────────
    TOKEN: str = os.getenv("DISCORD_TOKEN", "")
    CANAL_ID: int = int(os.getenv("CANAL_ID", "0"))

    # ── Agenda diária ─────────────────────────────────────────────────────────
    # Horário fixo para postar o resumo do dia (formato HH:MM, fuso BRT)
    HORARIO_AGENDA: str = os.getenv("HORARIO_AGENDA", "07:00")

    # ── Alertas ───────────────────────────────────────────────────────────────
    # Quantos minutos antes do evento enviar o alerta
    MINUTOS_ALERTA: int = int(os.getenv("MINUTOS_ALERTA", "15"))

    # ── Filtros de impacto ────────────────────────────────────────────────────
    # Impacto mínimo para aparecer na agenda diária (alto / medio / baixo)
    IMPACTO_MINIMO_AGENDA: str = os.getenv("IMPACTO_MINIMO_AGENDA", "baixo")

    # Impacto mínimo para receber alerta pré-evento e resultado
    IMPACTO_MINIMO_ALERTA: str = os.getenv("IMPACTO_MINIMO_ALERTA", "alto")

    # Filtro de moedas (ex: USD,EUR,GBP). Vazio = todas as moedas
    MOEDAS_FILTRO: set[str] = set(
        m.strip().upper() for m in os.getenv("MOEDAS_FILTRO", "").split(",") if m.strip()
    )

    # Fuso horário que o ForexFactory está servindo (depende do IP do servidor)
    # America/Sao_Paulo → servidor no Brasil (ForexFactory serve BRT)
    # America/New_York  → servidor nos EUA/neutro (ForexFactory serve ET)
    FOREX_TZ: str = os.getenv("FOREX_TZ", "America/New_York")

    @classmethod
    def validar(cls):
        erros = []
        if not cls.TOKEN:
            erros.append("DISCORD_TOKEN não definido no .env")
        if cls.CANAL_ID == 0:
            erros.append("CANAL_ID não definido no .env")
        if cls.HORARIO_AGENDA.count(":") != 1:
            erros.append("HORARIO_AGENDA deve estar no formato HH:MM (ex: 07:00)")
        if cls.IMPACTO_MINIMO_AGENDA not in ("alto", "medio", "baixo"):
            erros.append("IMPACTO_MINIMO_AGENDA deve ser alto, medio ou baixo")
        if cls.IMPACTO_MINIMO_ALERTA not in ("alto", "medio", "baixo"):
            erros.append("IMPACTO_MINIMO_ALERTA deve ser alto, medio ou baixo")
        if erros:
            raise EnvironmentError("Configuração inválida:\n" + "\n".join(erros))
