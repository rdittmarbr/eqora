# EQora Orquestrador (Raiz)

Orquestracao unica dos servicos:

- `api` (`app_api`)
- `web` (`app_client`)
- `admin` (`app_admin`)
- `web_mobile` (`app_mobile`)
- `db` + `redis`
- `nginx` (unico proxy reverso)

## Compose modular

O `build.sh` executa o Docker Compose combinando:

1. `docker-compose.yaml`
2. `docker-compose.db.yaml`
3. `docker-compose.api.yaml`
4. `docker-compose.web.yaml`
5. `docker-compose.mobile.yaml`
6. `docker-compose.nginx.yaml`
7. `docker-compose.<ambiente>.yaml`

Ambientes validos:

- `development`
- `homologation`
- `staging`
- `production`

## SERVER_TYPE (fallback)

O `build.sh` usa `SERVER_TYPE`:

- Se for `development|homologation|staging|production`, usa o valor informado.
- Se estiver ausente ou invalido, usa `development`.

## Arquivos de ambiente

Nao ha `.env` geral para a orquestracao.
Sempre é usado somente um arquivo por ambiente:

- `.env.development`
- `.env.homologation`
- `.env.staging`
- `.env.production`

## Nginx unico (default.conf)

Arquivo: `docker/nginx/default.conf`

Regras:

- URL iniciando com `/api` -> proxy para `api:8000` (Laravel com `php artisan serve`).
- URL iniciando com `/admin` -> proxy para `admin:3001`.
- Qualquer outra URL -> `web` (`app_client`).

## Regras por ambiente

- `development`: `web` roda `npm run dev`.
- `development`: `admin` roda `npm run dev`.
- `homologation`: `web` roda `npm run dev` (sem build).
- `homologation`: `admin` roda `npm run dev` (sem build).
- `staging`: frontend roda com build (`npm run build` + `npm run start`).
- `staging`: `admin` roda build + preview (`npm run build` + `npm run preview`).
- `production`: alem do proxy reverso, o container `nginx` mapeia `./app_client` em `/var/www/html/equora`.

Exposicao de portas:

- `development`: todas as portas sao expostas (`api`, `web`, `admin`, `web_mobile`, `nginx`, `db`, `redis`).
- `production`: somente a porta do `nginx` e exposta.

Observacao:

- A API roda com `php artisan serve` no compose base (`api:8000`), sem nginx dedicado para PHP.

## Comandos build.sh

- `./build.sh build`
- `./build.sh run`
- `./build.sh up`
- `./build.sh down`
- `./build.sh restart`
- `./build.sh logs`
- `./build.sh ps`
- `./build.sh config`
- `./build.sh validate`
- `./build.sh install`
- `./build.sh migration`
- `./build.sh all`
- `./build.sh mobile`

Opcoes uteis:

- `--service <nome>`: atua em um servico especifico (ex.: `api`, `web`, `nginx`).
- `--with-deps`: com `up --service`, sobe dependencias.
- `--dry-run`: imprime plano sem executar acoes destrutivas.
- `--seed`: com `migration`, executa seed.
- `-e, --env-file <arquivo>`: usa env file custom.
- `-f <arquivo>`: usa compose files custom (pode repetir).

`all`:

- faz `build` completo;
- em `production`, executa builds JS (`web_build`, `admin_build` e `web_mobile_build`);
- sobe a stack (`up -d`).

## Exemplo

```bash
SERVER_TYPE=development ./build.sh all
```

```bash
SERVER_TYPE=production ./build.sh all
```
