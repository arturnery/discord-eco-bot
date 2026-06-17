import requests
from bs4 import BeautifulSoup
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import logging
import re
import time

from config import Config

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

TZ_BRT = ZoneInfo("America/Sao_Paulo")

ORDEM_IMPACTO = {"alto": 0, "medio": 1, "baixo": 2}


class ForexFactoryScraper:
    URL_BASE = "https://www.forexfactory.com/calendar"

    def _get_com_retry(self, url: str, tentativas: int = 5, espera: int = 30):
        for i in range(tentativas):
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15)
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                logger.warning(f"Tentativa {i+1}/{tentativas} falhou ({e}). Aguardando {espera}s...")
                if i < tentativas - 1:
                    time.sleep(espera)
        logger.error(f"Todas as tentativas falharam para {url}")
        return None

    def buscar_eventos_dia(self) -> list[dict]:
        """Retorna todos os eventos do dia ordenados por impacto (alto→baixo) e horário."""
        resp = self._get_com_retry(self.URL_BASE)
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        eventos = self._parsear_eventos(soup, date.today())
        eventos.sort(key=lambda e: (
            ORDEM_IMPACTO.get(e["impacto"], 3),
            e.get("horario_dt") or datetime.max.replace(tzinfo=TZ_BRT),
        ))
        logger.info(f"{len(eventos)} eventos encontrados para hoje.")
        return eventos

    def buscar_eventos_semana(self) -> list[dict]:
        """Retorna todos os eventos dos próximos 7 dias ordenados por data, impacto e horário."""
        hoje = date.today()
        fim = hoje + timedelta(days=7)
        todos = []

        for url in [
            f"{self.URL_BASE}?week=this.week",
            f"{self.URL_BASE}?week=next.week",
        ]:
            resp = self._get_com_retry(url)
            if resp:
                soup = BeautifulSoup(resp.text, "html.parser")
                eventos = self._parsear_eventos_com_data(soup, hoje, fim)
                todos.extend(eventos)

        vistos = set()
        resultado = []
        for ev in todos:
            chave = f"{ev['titulo']}_{ev['data']}_{ev['horario']}"
            if chave not in vistos:
                vistos.add(chave)
                resultado.append(ev)

        resultado.sort(key=lambda e: (
            e["data"],
            ORDEM_IMPACTO.get(e["impacto"], 3),
            e["horario"],
        ))
        logger.info(f"{len(resultado)} eventos encontrados na semana.")
        return resultado

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _detectar_impacto(self, span) -> str | None:
        classes = " ".join(span.get("class", []))
        if "icon--ff-impact-red" in classes:
            return "alto"
        if "icon--ff-impact-ora" in classes:
            return "medio"
        if "icon--ff-impact-yel" in classes:
            return "baixo"
        return None

    def _parsear_eventos(self, soup: BeautifulSoup, hoje: date) -> list[dict]:
        eventos = []
        ultimo_horario_str = None
        data_atual: date | None = None
        ano_atual = hoje.year

        tabela = soup.find("table", class_="calendar__table")
        if not tabela:
            logger.warning("Tabela do calendário não encontrada.")
            return []

        for linha in tabela.find_all("tr", class_=re.compile(r"calendar__row")):
            try:
                classes = linha.get("class", [])

                if "calendar__row--day-breaker" in classes:
                    td_data = linha.find("td", class_="calendar__cell")
                    if td_data:
                        texto = td_data.get_text(separator=" ", strip=True)
                        try:
                            data_atual = datetime.strptime(f"{texto} {ano_atual}", "%a %b %d %Y").date()
                        except Exception:
                            pass
                    ultimo_horario_str = None
                    continue

                if data_atual != hoje:
                    continue

                impacto_td = linha.find("td", class_="calendar__impact")
                if not impacto_td:
                    continue
                span_impacto = impacto_td.find("span")
                if not span_impacto:
                    continue

                impacto = self._detectar_impacto(span_impacto)
                if not impacto:
                    continue

                moeda_td = linha.find("td", class_="calendar__currency")
                moeda = moeda_td.get_text(strip=True) if moeda_td else "?"

                titulo_td = linha.find("td", class_="calendar__event")
                titulo = titulo_td.get_text(strip=True) if titulo_td else "Sem título"

                horario_td = linha.find("td", class_="calendar__time")
                horario_str = horario_td.get_text(strip=True) if horario_td else ""
                if horario_str and horario_str not in ("", "All Day", "Tentative"):
                    ultimo_horario_str = horario_str
                elif not horario_str:
                    horario_str = ultimo_horario_str or "—"

                horario_str = horario_str or "—"

                horario_dt = None
                horario_brt = horario_str
                if horario_str and horario_str != "—":
                    horario_dt = self._converter_horario(horario_str, hoje)
                    if horario_dt:
                        horario_brt = horario_dt.astimezone(TZ_BRT).strftime("%H:%M")

                eventos.append({
                    "moeda": moeda,
                    "titulo": titulo,
                    "impacto": impacto,
                    "horario": horario_brt,
                    "horario_dt": horario_dt,
                    "anterior": self._texto_td(linha, "calendar__previous"),
                    "previsao": self._texto_td(linha, "calendar__forecast"),
                    "real": self._texto_td(linha, "calendar__actual") or None,
                })

            except Exception as e:
                logger.warning(f"Erro ao parsear linha: {e}")

        return eventos

    def _parsear_eventos_com_data(self, soup: BeautifulSoup, data_inicio: date, data_fim: date) -> list[dict]:
        eventos = []
        tabela = soup.find("table", class_="calendar__table")
        if not tabela:
            return []

        data_atual = None
        ultimo_horario_str = None
        ano_atual = date.today().year

        for linha in tabela.find_all("tr", class_=re.compile(r"calendar__row")):
            try:
                classes = linha.get("class", [])

                if "calendar__row--day-breaker" in classes:
                    td_data = linha.find("td", class_="calendar__cell")
                    if td_data:
                        texto = td_data.get_text(separator=" ", strip=True)
                        try:
                            data_atual = datetime.strptime(f"{texto} {ano_atual}", "%a %b %d %Y").date()
                        except Exception:
                            pass
                    ultimo_horario_str = None
                    continue

                if data_atual is None or not (data_inicio <= data_atual <= data_fim):
                    continue

                impacto_td = linha.find("td", class_="calendar__impact")
                if not impacto_td:
                    continue
                span_impacto = impacto_td.find("span")
                if not span_impacto:
                    continue

                impacto = self._detectar_impacto(span_impacto)
                if not impacto:
                    continue

                moeda_td = linha.find("td", class_="calendar__currency")
                moeda = moeda_td.get_text(strip=True) if moeda_td else "?"

                titulo_td = linha.find("td", class_="calendar__event")
                titulo = titulo_td.get_text(strip=True) if titulo_td else "Sem título"

                horario_td = linha.find("td", class_="calendar__time")
                horario_str = horario_td.get_text(strip=True) if horario_td else ""
                if horario_str and horario_str not in ("", "All Day", "Tentative"):
                    ultimo_horario_str = horario_str
                elif not horario_str:
                    horario_str = ultimo_horario_str or "—"

                horario_str = horario_str or "—"

                horario_dt = None
                horario_brt = horario_str
                if horario_str and horario_str != "—":
                    horario_dt = self._converter_horario(horario_str, data_atual)
                    if horario_dt:
                        horario_brt = horario_dt.astimezone(TZ_BRT).strftime("%H:%M")

                eventos.append({
                    "data": data_atual,
                    "moeda": moeda,
                    "titulo": titulo,
                    "impacto": impacto,
                    "horario": horario_brt,
                    "horario_dt": horario_dt,
                    "anterior": self._texto_td(linha, "calendar__previous"),
                    "previsao": self._texto_td(linha, "calendar__forecast"),
                    "real": self._texto_td(linha, "calendar__actual") or None,
                })

            except Exception as e:
                logger.warning(f"Erro ao parsear linha: {e}")

        return eventos

    def _texto_td(self, linha, classe: str) -> str:
        td = linha.find("td", class_=classe)
        return td.get_text(strip=True) if td else ""

    def _converter_horario(self, horario_str: str, dia: date):
        try:
            horario_str = horario_str.lower().replace(" ", "")
            dt_naive = datetime.strptime(f"{dia} {horario_str}", "%Y-%m-%d %I:%M%p")
            return dt_naive.replace(tzinfo=ZoneInfo(Config.FOREX_TZ))
        except Exception:
            return None
