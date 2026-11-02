# gitai 

> Nome de trabalho: **gitai** (pode mudar). Rodando como subcomando do Git: `git ai ...`.
> Status: rascunho de design, antes da primeira linha de código.

## 1. Resumo

Ferramenta em Python que pega **todas as mudanças que você fez** (depois de um `git add -A` ou mesmo sem ele), **separa em commits por contexto** (um sobre login, outro sobre estilização de uma página, etc.), escreve as mensagens e, depois do push, **posta um comentário descritivo em cada commit** pela API REST do GitHub.

A IA **organiza e descreve código que é seu**. Ela nunca escreve nem altera código. Todo conteúdo do repositório continua sendo autoria do desenvolvedor, e ele consegue explicar cada linha.

Para não depender de um único provedor gratuito de LLM, a ferramenta inclui um **router com fallback automático** entre 3 ou 4 provedores (mais um modelo local). O router é exposto como uma **API REST em FastAPI**.

## 2. Objetivos

### Objetivos do produto

1. Dividir um conjunto grande de mudanças em commits **coerentes e atômicos**, por contexto.
2. Gerar mensagens de commit no padrão **Conventional Commits**, descritivas e curtas.
3. Garantir que **nenhuma linha seja perdida, duplicada ou alterada**: o resultado final tem que ser idêntico ao working tree original.
4. Nunca perder trabalho: backup antes de qualquer operação, com comando de desfazer.
5. Postar um comentário descritivo em cada commit via **GitHub REST API**, de forma idempotente.
6. Continuar funcionando quando um provedor de IA esgotar a cota: **fallback automático** entre provedores.
7. Funcionar offline em modo degradado (agrupamento determinístico, sem IA).

### Objetivos de aprendizado

- **Consumir REST:** GitHub API e APIs de LLM (autenticação, paginação, rate limit, retry, tratamento de erros).
- **Expor REST:** API em FastAPI (schemas Pydantic, validação, erros padronizados, autenticação local, docs OpenAPI).
- Internos do Git: índice, hunks, `git apply --cached`, refs, árvores.
- Concorrência com `async`/`httpx`, testes com repositórios temporários, avaliação de prompts.

### Não-objetivos (de propósito)

- **Não** altera datas de commits, não cria commits vazios ou "heartbeat" e não fabrica atividade para o gráfico de contribuições.
- **Não** reescreve histórico já enviado (sem `force push`, sem `rebase` automático).
- **Não** deixa a IA escrever ou modificar código-fonte.
- **Não** é um revisor de código crítico no histórico público (ver seção 9).
- **Não** depende de back-end hospedado: tudo roda na máquina do usuário.

## 3. Decisões de design

| Decisão | Escolha | Motivo |
|---|---|---|
| Instalação | Uma vez na máquina (`pipx install`), vale para todos os repositórios | Não precisa instalar por projeto |
| Invocação | Executável `git-ai` no PATH, usado como `git ai ...` | O Git reconhece `git-<nome>` como subcomando |
| Hook ou comando? | **Comando**, não hook | Hooks rodam durante um commit; aqui precisamos criar vários |
| Papel da IA | Só **agrupa IDs de hunks** e **escreve texto** | Impede que ela invente ou altere código |
| Config por repo | `.gitai.toml` opcional na raiz | Regras diferentes por projeto (idioma, provedores permitidos) |
| CLI e API | CLI usa o `core` direto; a API é uma camada fina por cima | A ferramenta funciona sem servidor rodando |
| Provedores | Interface única compatível com `/chat/completions` (formato OpenAI) | A maioria dos provedores gratuitos aceita esse formato |
| Armazenamento local | SQLite (uso e cooldown dos provedores, planos) | Simples, sem serviço externo |

## 4. Arquitetura

```
                 ┌───────────┐     ┌──────────────────────┐
  git ai ... ──► │    CLI    │ ──► │         core         │ ◄── tests
                 └───────────┘     │  gitops · hunks      │
                                   │  planner · applier   │
  HTTP (local) ► ┌───────────┐ ──► │  secrets · messages  │
                 │  FastAPI  │     └─────────┬────────────┘
                 └───────────┘               │
                                  ┌──────────┴───────────┐
                                  ▼                      ▼
                          ┌──────────────┐      ┌────────────────┐
                          │ LLM router   │      │ GitHub client  │
                          │ fallback,    │      │ (comentários   │
                          │ cooldown     │      │  em commits)   │
                          └──────┬───────┘      └────────────────┘
                   Gemini · Groq · OpenRouter · Ollama (local)
```

### Estrutura de pastas

```
gitai/
├── pyproject.toml
├── DESIGN.md
├── README.md                 # escrito depois, descreve uso
├── src/gitai/
│   ├── cli.py                # Typer: plan, commit, undo, comment, serve, providers
│   ├── config.py             # carrega ~/.config/gitai/config.toml e .gitai.toml
│   ├── core/
│   │   ├── gitops.py         # wrappers de subprocess para o git
│   │   ├── hunks.py          # parse de `git diff` em Hunks com IDs
│   │   ├── planner.py        # prompt, chamada ao router, validação do plano
│   │   ├── applier.py        # backup, git apply --cached, commit, verificação, undo
│   │   ├── secrets.py        # varredura antes de enviar o diff a um LLM
│   │   └── messages.py       # regras determinísticas de mensagem
│   ├── router/
│   │   ├── providers.py      # cliente httpx por provedor
│   │   ├── router.py         # estratégia, fallback, circuit breaker
│   │   ├── usage.py          # contadores e cooldown em SQLite
│   │   └── errors.py
│   ├── github/
│   │   ├── client.py         # httpx + auth + rate limit
│   │   └── comments.py       # montagem e postagem idempotente
│   └── api/
│       ├── app.py            # FastAPI
│       ├── routes.py
│       └── schemas.py        # modelos Pydantic
├── tests/
└── evals/                    # conjunto de diffs reais para comparar modelos
```

## 5. Fluxo principal: `git ai commit`

1. **Pré-checagens.** Está num repositório Git, não há merge ou rebase em andamento, existem mudanças.
2. **Backup.** Cria uma ref privada `refs/gitai/backups/<timestamp>` apontando para uma árvore com o estado atual do working tree, **incluindo arquivos novos**. Usa um índice temporário (`GIT_INDEX_FILE`), então não mexe no índice do usuário.
3. **Coleta.** Arquivos novos (não ignorados) entram com `git add -N`. A fonte das mudanças é `git diff HEAD`, o que inclui o que estava staged ou não. O índice atual é ignorado: o estado dele já está no backup.
4. **Hunks.** O diff é quebrado em hunks, cada um com ID estável (`h1`, `h2`...) e metadados: arquivo, tipo (modificado, novo, removido, renomeado, binário), linhas, trecho.
5. **Filtro e segurança.** Remove do envio lockfiles, binários, arquivos gerados e qualquer coisa que a varredura de segredos marque (seção 8). Para arquivos filtrados, só o nome e a estatística vão ao LLM.
6. **Planejamento.** O planner manda os hunks (com IDs) ao router e pede um JSON de commits. O LLM **só devolve IDs e texto**.
7. **Validação.** Aplica as regras da seção 6. Se falhar, devolve o erro ao LLM e pede nova tentativa (até 2). Persistindo a falha, passa ao próximo provedor. Se todos falharem, usa o **agrupamento determinístico** (por diretório de primeiro nível) e avisa.
8. **Confirmação.** Mostra o plano (commits, arquivos, mensagens) e pergunta. `--dry-run` para aqui, `--yes` pula a pergunta.
9. **Aplicação.** Zera o índice (`git reset`, o working tree fica intacto) e, para cada commit planejado, monta um patch só com os hunks do grupo, roda `git apply --cached` e depois `git commit`.
10. **Verificação do invariante.** Ao final, `git diff HEAD` precisa estar **vazio**, ou seja, a soma dos commits reproduz exatamente o working tree. Se não estiver, o programa restaura do backup automaticamente e relata o erro.
11. **Resumo.** Lista os commits criados e o caminho para desfazer (`git ai undo`).

### Comandos

| Comando | O que faz |
|---|---|
| `git ai plan` | Gera e mostra o plano, sem alterar nada (igual a `commit --dry-run`) |
| `git ai commit` | Gera o plano, pede confirmação e cria os commits |
| `git ai undo` | Restaura o working tree e o HEAD a partir do último backup |
| `git ai comment` | Posta comentários nos commits já enviados (seção 7) |
| `git ai providers` | Mostra o estado dos provedores (uso do dia, cooldown) |
| `git ai serve` | Sobe a API FastAPI local |

## 6. Contrato com o LLM e validação

### Saída exigida

```json
{
  "commits": [
    {
      "type": "feat",
      "scope": "login",
      "summary": "add email validation to login form",
      "body": "Reject malformed addresses before the request is sent.",
      "hunk_ids": ["h1", "h4", "h5"]
    }
  ]
}
```

### Regras determinísticas (código Python, sem IA)

1. JSON válido e de acordo com o schema Pydantic.
2. Todo `hunk_id` existe, e **cada hunk aparece exatamente uma vez** em todo o plano.
3. Nenhum commit vazio.
4. Título no formato `tipo(escopo): resumo`, com `tipo` numa lista permitida (`feat`, `fix`, `refactor`, `style`, `docs`, `test`, `chore`, `perf`, `build`, `ci`).
5. Título com no máximo 72 caracteres, sem ponto final.
6. Rejeita títulos genéricos (`update`, `fix stuff`, `changes`, `wip`...).
7. Corpo com no máximo 3 linhas.

### Regras no prompt (julgamento do modelo)

- Um commit equivale a uma mudança lógica.
- Não misturar refactor com feature, nem estilo com lógica.
- Manter juntos hunks que dependem um do outro, para o projeto não ficar quebrado entre commits.
- A mensagem descreve **o quê** e, quando der, **por quê**. Descreve, não avalia.
- Na dúvida, menos commits e maiores.

### Entrada não confiável

O diff é **dado**, não instrução. Um comentário no código dizendo "ignore as instruções anteriores" não pode mudar o comportamento. O prompt separa claramente instruções e conteúdo, e a saída só é aceita se passar pelo schema. Como o LLM só devolve IDs e texto curto, o dano possível já é limitado.

## 7. Comentários nos commits (GitHub REST API)

### Endpoint

`POST /repos/{owner}/{repo}/commits/{commit_sha}/comments` com corpo `{"body": "..."}`.
Para listar os existentes: `GET /repos/{owner}/{repo}/commits/{commit_sha}/comments`.
**Verificar na documentação oficial** as permissões exatas do token fine-grained antes de implementar.

### Regras

- O commit precisa **já estar no remoto**. O comando roda depois do `git push`.
- **Opt-in por repositório** (`comments.enabled = true`), porque os comentários ficam visíveis na página do commit.
- **Idempotente:** cada comentário leva um marcador oculto (`<!-- gitai:v1 -->`). Antes de postar, lista os comentários do commit e pula se o marcador já existir.
- **Descrever, não avaliar:** "Adiciona validação de e-mail no formulário de login" e não "Ótima implementação". Texto curto, de 2 a 4 linhas.
- **Respeita rate limit:** pausa entre postagens, lê os cabeçalhos de limite e trata `403`/`429` com espera.
- **Token:** lido de variável de ambiente ou do keyring do sistema, nunca gravado no repositório. Usar token com escopo mínimo, restrito aos repositórios necessários.
- Descobre `owner/repo` pelo remoto `origin`.

## 8. Router de provedores de LLM

### Provedores iniciais

Gemini (família Flash), Groq, OpenRouter (modelos `:free`) e Ollama local como último recurso. Todos usam o formato `/chat/completions`, então um cliente `httpx` único serve a todos, mudando `base_url`, chave e modelo. Os limites dos planos gratuitos mudam sem aviso, por isso ficam na **configuração**, não no código.

### Configuração (exemplo)

```toml
[router]
strategy = "priority"        # ou "round_robin"
request_timeout_s = 60

[[providers]]
name = "gemini"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
api_key_env = "GEMINI_API_KEY"
model = "<confirmar nome do modelo na documentação>"
daily_request_limit = 500    # ajuste conforme o seu painel

[[providers]]
name = "groq"
base_url = "https://api.groq.com/openai/v1"
api_key_env = "GROQ_API_KEY"
model = "openai/gpt-oss-120b"
daily_request_limit = 1000

[[providers]]
name = "ollama"
base_url = "http://localhost:11434/v1"
api_key_env = ""
model = "qwen2.5-coder"
local = true                 # nunca sai da máquina
```

### Comportamento

| Situação | Reação |
|---|---|
| `429` (limite) | Cooldown usando `Retry-After`, ou até o reset diário se a cota do dia acabou; passa ao próximo |
| `401` / `403` | Desabilita o provedor na sessão e avisa que a chave está errada |
| `5xx` ou timeout | 1 nova tentativa com backoff; depois, próximo provedor |
| `400` por contexto longo | Próximo provedor com janela maior, ou divide o diff em partes |
| JSON inválido | Reenvia com a mensagem de erro (até 2 vezes); depois, próximo provedor |
| Falhas repetidas | **Circuit breaker:** tira o provedor da rotação por alguns minutos |

- **Contador local** de requisições e tokens por provedor/dia (SQLite), para pular um provedor **antes** de estourar a cota. Os horários de reset variam por provedor.
- **Log** de qual provedor respondeu cada chamada, porque a qualidade varia entre modelos.
- **Uma conta por provedor.** Criar várias contas no mesmo provedor para driblar limites costuma violar os termos de uso e não faz parte do projeto.

### Privacidade e segredos

- Varredura **antes** de enviar qualquer diff: padrões de tokens e chaves, `.env*`, `*.pem`, `id_rsa`, etc. Se algo for encontrado, o envio é bloqueado (ou o trecho é omitido, por configuração).
- **Allowlist de provedores por repositório** no `.gitai.toml`. Código de trabalho pode ficar restrito ao Ollama local.
- Alguns planos gratuitos usam os dados para treino de modelos. Ler a política de cada provedor antes de ativá-lo.
- Limite de tamanho do diff enviado, com aviso quando for excedido.

## 9. API REST (FastAPI)

Roda só em `127.0.0.1`, por padrão em `git ai serve`.

| Método e rota | Função |
|---|---|
| `GET /health` | Verificação de saúde |
| `GET /v1/providers` | Estado dos provedores: uso do dia, cooldown, habilitado |
| `POST /v1/chat/completions` | Router compatível com o formato da OpenAI (usa o fallback) |
| `POST /v1/plans` | Gera um plano de commits para um repositório registrado |
| `GET /v1/plans/{id}` | Consulta um plano |
| `POST /v1/plans/{id}/apply` | Aplica o plano |
| `POST /v1/comments` | Gera e posta comentários para uma lista de commits |

### Segurança da API local

Uma API local que executa `git` em caminhos do disco precisa de cuidado, porque qualquer página aberta no navegador consegue fazer requisições para `localhost`.

- Escuta apenas em `127.0.0.1`.
- Exige um **token local** em cabeçalho, gerado na primeira execução.
- Aceita só **repositórios registrados** na config, nunca um caminho arbitrário.
- Sem CORS aberto.
- Erros padronizados (RFC 7807 ou formato próprio consistente), nunca vazando chaves nem caminhos sensíveis.

### Revisão crítica fica fora do histórico público

Se a ferramenta apontar possíveis bugs ou pontos de atenção em um commit, isso aparece **só no terminal** (ou em nota local), não no GitHub. O comentário público segue a regra de descrever, não avaliar.

## 10. Modelos de dados (Pydantic)

```python
class Hunk(BaseModel):
    id: str                 # "h1", "h2"...
    file: str
    kind: Literal["modified", "added", "deleted", "renamed", "binary"]
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    patch: str              # trecho do diff, com cabeçalho do arquivo

class PlannedCommit(BaseModel):
    type: str
    scope: str | None
    summary: str
    body: str | None
    hunk_ids: list[str]

class Plan(BaseModel):
    id: str
    repo: str
    base_sha: str
    backup_ref: str
    provider_used: str | None   # None se foi o agrupamento determinístico
    commits: list[PlannedCommit]
```

## 11. Riscos e como mitigar

| Risco | Mitigação |
|---|---|
| Commit intermediário que não compila (hunks dependentes separados) | Prompt manda manter relacionados juntos; opção `--run-tests` roda os testes após cada commit e para se falhar |
| Hunks do mesmo arquivo em commits diferentes mudam as linhas dos seguintes | `git apply` tolera deslocamento por contexto; coberto por testes específicos |
| Hunks que o Git funde (mudanças próximas) | Aceito na v1: ficam no mesmo commit. Divisão fina pode vir depois |
| Perda de trabalho | Backup em ref privada + verificação do invariante + `git ai undo` |
| Arquivos novos, removidos, renomeados e binários | Tratados como tipos de hunk próprios; binários vão inteiros a um commit |
| Provedor gratuito muda limites ou some | Limites na config, fallback automático, Ollama local como rede de segurança |
| Vazamento de segredos para um provedor externo | Varredura prévia, allowlist por repo, modo local |
| Hooks de pre-commit do usuário falham no meio | Para e restaura; opção `--no-verify` explícita |
| Mensagens com cara genérica ou de IA | Regras determinísticas, limite de tamanho, estilo "descrever, não avaliar", revisão no `--dry-run` |

## 12. Testes e avaliação

### Testes automatizados (pytest)

- **Repositórios temporários:** fixtures criam repos com `git init` em `tmp_path` e cenários prontos (arquivo novo, removido, renomeado, binário, mudanças no mesmo arquivo em lugares distantes).
- **Invariante central:** depois de `commit`, `git diff HEAD` é vazio e a árvore final é idêntica ao backup.
- **Parser de hunks:** testes com diffs de exemplo (golden files).
- **Router:** provedores simulados (por exemplo com `respx`) devolvendo `429`, `5xx`, timeout e JSON inválido, verificando fallback, cooldown e circuit breaker.
- **GitHub:** cliente com respostas simuladas, incluindo idempotência e rate limit.
- **API:** `TestClient` do FastAPI, incluindo rejeição de token ausente e de repo não registrado.
- Qualidade de código: `ruff` e `mypy` no CI.

### Avaliação de modelos (`evals/`)

Montar de 10 a 15 diffs reais seus e rodar o mesmo plano em cada provedor. Métricas: taxa de JSON válido na primeira tentativa, taxa de planos aprovados na validação, nota manual (1 a 5) para a qualidade do agrupamento e das mensagens, latência. Os resultados ficam versionados e servem para decidir a ordem de prioridade do router.

## 13. Roadmap

| Etapa | Entrega | Pronto quando |
|---|---|---|
| **M0** | Parser de hunks, backup, aplicação e `undo`, **sem IA** (agrupamento por diretório) | Invariante passa em todos os cenários de teste |
| **M1** | Planner com um provedor, validação e `--dry-run` | Planos válidos em 10 diffs reais, com revisão manual |
| **M2** | Router com fallback, cooldown e contadores | Testes simulando 429/5xx passam; troca de provedor sem intervenção |
| **M3** | API FastAPI (`/v1/chat/completions`, `/v1/plans`, `/v1/providers`) com token local | Docs OpenAPI no ar; o CLI funciona com e sem o servidor |
| **M4** | Comentários no GitHub (idempotentes) | Rodar duas vezes não duplica; rate limit tratado |
| **M5** | Varredura de segredos, `.gitai.toml`, allowlist, README e empacotamento com `pipx` | Instalação limpa em máquina nova |

Ordem proposital: o núcleo sem IA primeiro, porque é a parte mais difícil e a que mais ensina, e a IA entra depois sobre uma base que já funciona.

## 14. Perguntas em aberto

1. **Nome final** do projeto e do executável.
2. **Idioma das mensagens** de commit (inglês ou português), configurável por repositório.
3. **Granularidade:** na v1, hunks do Git; vale dividir hunks maiores depois?
4. **Testes por commit** habilitados por padrão ou só sob demanda?
5. **Onde guardar planos** (SQLite local ou só em memória)?
6. **Permissões exatas** do token do GitHub para comentar em commits (conferir na documentação).

## 15. Glossário

- **Hunk:** bloco contínuo de mudança dentro de um arquivo, como aparece no `git diff`.
- **Índice (staging area):** o que será incluído no próximo commit.
- **Conventional Commits:** padrão `tipo(escopo): resumo` para mensagens de commit.
- **Cooldown:** período em que um provedor é evitado depois de atingir um limite.
- **Circuit breaker:** padrão que tira um serviço instável de circulação por um tempo, em vez de insistir nele.
- **Idempotente:** executar duas vezes dá o mesmo resultado que executar uma.
