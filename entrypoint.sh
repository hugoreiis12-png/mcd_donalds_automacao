#!/bin/sh
set -e

mkdir -p /app/dados/logs /app/dados/xlsx /app/dados/csv
touch /app/dados/logs/cron.log

# Timezone: permite sobrescrever o TZ da imagem em runtime (env do compose).
if [ -n "$TZ" ] && [ -f "/usr/share/zoneinfo/$TZ" ]; then
    ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime
    echo "$TZ" > /etc/timezone
fi

# O binario tem que existir ANTES de agendar qualquer coisa. Sem esta checagem,
# um console script ausente so se revela as 08:00 do dia seguinte, com um
# "mcd-donalds: not found" no cron.log e nenhuma carga feita.
if ! command -v mcd-donalds > /dev/null 2>&1; then
    echo "[entrypoint] ERRO: 'mcd-donalds' nao encontrado no PATH ($PATH)." >&2
    echo "[entrypoint] A imagem foi construida sem 'pip install .'? Rebuild necessario." >&2
    exit 1
fi

# O cron nao herda o ambiente do container. Exportamos as variaveis relevantes
# para um arquivo que o job carrega (. /app/container_env.sh) antes de rodar o
# pipeline. Valores em aspas simples para tolerar espacos/&/? (ex: SITE_URL).
#
# PATH entra na lista de proposito: o PATH default do cron ("/usr/bin:/bin") nao
# tem /usr/local/bin, onde vive o console script instalado pelo pip. O crontab ja
# declara um PATH proprio; exportar aqui e a segunda camada de defesa.
printenv | grep -E '^(PATH|PG_[A-Z0-9_]*|BASE_DIR|SITE_URL|LOGIN_URL|LOGIN_USER|LOGIN_PASSWORD|HEADLESS|CANAL|NOTIFICAR_SUCESSO|VERIFICAR_SITE|DT_INICIO_EXTRACAO|DOWNLOAD_TIMEOUT|RETENCAO_DIAS|CHROME_BINARY|CHROMEDRIVER_PATH|TZ|RUN_ON_START)=' \
    | while IFS='=' read -r chave valor; do
        printf "export %s='%s'\n" "$chave" "$valor"
    done > /app/container_env.sh

echo "[entrypoint] $(date '+%Y-%m-%d %H:%M:%S %Z') — container pronto."
echo "[entrypoint] Agendamento: todo dia 08:00 (${TZ:-UTC})."

# Execucao imediata opcional — util para validar o deploy sem esperar o horario.
if [ "${RUN_ON_START:-false}" = "true" ]; then
    echo "[entrypoint] RUN_ON_START=true — executando pipeline agora..."
    . /app/container_env.sh
    mcd-donalds >> /app/dados/logs/cron.log 2>&1 \
        || echo "[entrypoint] pipeline retornou erro (segue para o agendador)."
fi

# Sobe o cron (daemoniza) e mantem o PID 1 vivo espelhando o log no stdout,
# assim as execucoes ficam visiveis nos logs do container (Portainer).
cron
exec tail -F /app/dados/logs/cron.log
