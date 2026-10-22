# Pipeline de deploy: da release ao contêiner

Publicar uma release no GitHub atualiza o Securo no servidor, sem passos manuais depois.

```
 você publica a release vX.Y.Z (maranguapo/securo)
        │
        ▼
 release.yml  ──►  constrói as imagens (amd64 + arm64)
                   ghcr.io/maranguapo/securo-backend:X.Y.Z
                   ghcr.io/maranguapo/securo-frontend:X.Y.Z
        │
        ▼  (job "deploy")
 commit em maranguapo/mrg-home-services
   docker-stacks/securo/stack.yaml  → tags passam a ser X.Y.Z
        │
        ▼  (pipeline que você já tinha)
 securo-workflow.yml → n8n → webhook do Portainer → docker compose up
```

O elo novo é o job `deploy` do `.github/workflows/release.yml`. Ele só troca as duas
referências de imagem do `stack.yaml`; nada mais no arquivo é tocado. Se o arquivo já
aponta para a versão, não faz commit.

## Configuração (uma vez)

1. **Branch com o código na `main`.** O workflow de release roda a partir do commit da tag,
   então ele precisa existir na `main` antes da primeira release.
2. **Chave de deploy para escrever no repositório da infraestrutura.** Um par de chaves SSH:
   a pública fica como *deploy key com escrita* **só** em `maranguapo/mrg-home-services`
   (Settings → Deploy keys), e a privada como o segredo `HOME_SERVICES_DEPLOY_KEY` em
   `maranguapo/securo` (Settings → Secrets and variables → Actions). Para refazer:
   ```bash
   ssh-keygen -t ed25519 -N "" -C securo-release-deploy -f /tmp/dk
   gh api repos/maranguapo/mrg-home-services/keys -f title=securo-release-deploy \
      -f key="$(cat /tmp/dk.pub)" -F read_only=false
   gh secret set HOME_SERVICES_DEPLOY_KEY -R maranguapo/securo < /tmp/dk
   rm /tmp/dk /tmp/dk.pub
   ```
   Não é o `GITHUB_TOKEN` padrão porque um push feito com ele não dispara outros workflows, e
   o `securo-workflow.yml` nunca rodaria. Um push por chave de deploy dispara normalmente. Vale
   para um repositório só e não expira. Sem o segredo, a release continua publicando as
   imagens e o job apenas avisa que não fez deploy.
3. **Imagens acessíveis ao Portainer.** Depois da primeira release, em GitHub → seu perfil →
   *Packages* → `securo-backend` e `securo-frontend` → *Package settings* → *Change
   visibility* → **Public**. (Alternativa: manter privado e cadastrar o GHCR em
   Portainer → Registries com um token `read:packages`.)
4. **Tirar as variáveis de imagem do Portainer.** Em Portainer → Stacks → securo →
   Environment variables, apague `BACKEND_IMAGE` e `FRONTEND_IMAGE` (se existirem). Elas
   têm precedência sobre o `stack.yaml` e fariam o Portainer ignorar a versão nova.
5. **Desativar o que veio do projeto original e não serve ao fork**, para não gerar execuções
   vermelhas todo dia:
   `gh workflow disable downloads-badge.yml -R maranguapo/securo`
6. *(Opcional)* Segredo `RELEASE_TOKEN` (token com *Contents* e *Pull requests*: read/write
   em `maranguapo/securo`) para o CI rodar no PR de release aberto pelo workflow.

## Publicar uma versão

1. GitHub → Actions → **Prepare Release** → *Run workflow* → informe a versão (ex.: `0.17.0`,
   sem o `v`). Ele abre um PR que ajusta os números de versão.
2. Revise e faça o merge do PR na `main`.
3. GitHub → Releases → **Draft a new release** → tag `v0.17.0` (criada a partir da `main`) →
   **Publish release**. Uma *pre-release* constrói as imagens, mas **não faz deploy**.
4. Acompanhe em Actions: *Build and Push Docker Images* (builds de amd64 e arm64 levam de
   10 a 30 minutos; o de arm64 é emulado) → depois o job *deploy* → o commit
   `deploy(securo): v0.17.0` aparece em `mrg-home-services` → o `securo-workflow.yml` roda →
   o Portainer atualiza.

## Antes de uma release com migration

O backend roda `alembic upgrade head` ao iniciar, então o deploy aplica as migrations no
banco de produção sozinho, e elas só andam para frente. Faça um backup antes de publicar
qualquer versão que adicione arquivos em `backend/alembic/versions/`:

```bash
docker exec -t postgres pg_dump -U postgres -Fc securo > securo-$(date +%F).dump
```

## Implantar uma versão à mão, simular, e voltar atrás (rollback)

O deploy mora em `.github/workflows/deploy-stack.yml`, que a release chama, mas que também
roda sozinho: GitHub → Actions → **Deploy stack** → *Run workflow*.

- **Rollback ou avanço:** informe a versão (ex.: `0.16.4`). Ele registra essas tags no
  `stack.yaml` e o pipeline redeploya.
- **Simulação:** marque *dry_run*. Clona o repositório de infra, faz a edição e roda
  `git push --dry-run`, que checa a permissão de escrita da chave, **sem enviar nada**. Use
  depois de mexer em chaves, segredos ou no caminho do arquivo.

Também dá para reverter o commit `deploy(securo): vX.Y.Z` direto em `mrg-home-services`.
O pipeline redeploya a imagem antiga. Atenção: **isso não desfaz
migrations**. Se a versão nova alterou o banco de um jeito que a antiga não entende, restaure
o backup do passo anterior junto com o rollback.

## Quando algo não anda

| Sintoma | Causa provável |
|---|---|
| O job *deploy* diz "HOME_SERVICES_DEPLOY_KEY is not set" | segredo ausente ou com outro nome |
| *deploy* falha em "Permission denied (publickey)" | a deploy key foi removida do `mrg-home-services` ou não tem escrita |
| *deploy* falha em "not found in …" | o caminho do `stack.yaml` mudou; ajuste a variável `STACK_FILE` do repositório |
| Commit criado, mas o `securo-workflow.yml` não roda | o commit não alterou o `stack.yaml`, ou o workflow do repositório de infra está desativado |
| Portainer sobe a versão antiga | `BACKEND_IMAGE`/`FRONTEND_IMAGE` ainda definidos no ambiente do Portainer |
| Portainer: "pull access denied" | pacote do GHCR ainda privado (passo 3) |
| `exec format error` no Pi | a imagem não tem arm64; confira se o build de `linux/arm64` terminou |

Variáveis opcionais do repositório (Settings → Variables): `STACK_REPO` (padrão
`maranguapo/mrg-home-services`), `STACK_FILE` (padrão `docker-stacks/securo/stack.yaml`) e
`PUBLISH_HELM_CHART=true` para voltar a publicar o chart do Helm.
