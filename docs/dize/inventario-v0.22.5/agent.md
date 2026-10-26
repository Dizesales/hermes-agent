# Inventário Dize — núcleo `agent/*` e afins (Hermes v0.22.1 → v0.22.5)

Escopo: `agent/*` do delta, `tools/vision_tools.py`, `hermes_cli/auth.py`, `hermes_cli/config_defaults.py`,
`cli-config.yaml.example`, `website/docs/user-guide/features/skills.md` e testes correspondentes.

- Delta analisado: `git diff v0.22.1^{commit} fork/hermes-hefesto` (só leitura).
- Mudanças upstream no mesmo código: `git diff v0.22.1^{commit} v0.22.5^{commit}`.
- Árvore de prova própria: `scratchpad/wt-agent` = v0.22.5 + todos os arquivos já aplicados do
  `probe/v0.22.5-apply` + os 3 conflitos resolvidos deste escopo. Os 6 conflitos de `gateway/*` ficaram na versão
  v0.22.5 pura (estão fora deste escopo). Árvore de controle: `scratchpad/wt-base` = v0.22.5 pura.
- Interpretador: `/usr/local/lib/hermes-agent/venv/bin/python` (3.14), só para ler/importar. pytest foi instalado à
  parte em `scratchpad/pydeps`; nada foi escrito em `/usr/local/lib/hermes-agent`. TMPDIR próprio.

## Resultados dos testes (árvore mesclada)

| Conjunto | Árvore mesclada | v0.22.5 pura |
|---|---|---|
| Testes Dize do escopo (10 arquivos, 464 testes) | **464 passed** | — |
| Upstream vizinhos (credential_pool*, aux*, prompt_builder, turn_context*, turn_finalizer*, context_compressor*, codex_runtime*, vision_tools, auth_pool_operations: 1018) | 1015 passed, 1 failed, 2 skipped | 1014 passed, **o mesmo** 1 failed, 2 skipped |
| Probe Dize criado agora (`wt-agent/tests/agent/test_dize_inventory_probe.py`, 17 casos) | **17 passed** | 14 failed / 3 passed |
| Grande varredura (todo teste que cita stop gates, pre_verify, compaction nativa, codex session, skills prompt, select_context, idle, token_is_blocked, merge de disco, tool_executor) | ver "Varredura ampla" no fim | — |

A falha `tests/hermes_cli/test_auth_pool_operations.py::test_add_priority_places_reauthenticated_row_in_multi_entry_pool`
só ocorre na execução combinada e **também falha na v0.22.5 pura**. Isolada, passa nas duas. É um teste upstream
que depende da ordem; não é regressão do delta.

O probe falhar na v0.22.5 pura prova que **nenhuma** dessas correções Dize já existe no upstream (credenciais,
aux Anthropic, compressor OAuth, pre_verify_on_stop, MIME de visão, piso de idle). Os 3 casos que passam nas duas
são: `pre_verify_on_stop=False` e `"true"` (string), que dão `None` nas duas, e a preservação das chaves de config
(isso já é comportamento do upstream).

## Tabela-resumo

| id | Mudança | Quem precisa | v0.22.5 | Risco Hefesto |
|---|---|---|---|---|
| AG-01 | Pool mantém o prazo 429 conhecido quando o retry vem sem reset | frota (crítico p/ Hefesto) | MANTER | alto |
| AG-02 | `token_is_blocked` trata credencial esgotada ou morta como bloqueada | frota (Hefesto) | MANTER | alto |
| AG-03 | Merge de disco do auth não encurta o prazo 429 | frota (Hefesto) | MANTER | alto |
| AG-04 | Cliente aux Anthropic escolhe por modelo e recusa token bloqueado, inclusive em cache | frota (Hefesto) | MANTER (conflito resolvido) | alto |
| AG-05 | Aux Anthropic em cooldown cai para a cadeia principal de fallback | frota (Hefesto) | MANTER (conflito resolvido) | médio |
| AG-06 | Rotação aux usa erro estruturado; sem retry na mesma chave se o reset ainda é futuro | frota | MANTER | médio |
| AG-07 | Idle compaction usa o relógio durável da SessionDB | Hefesto (gateway) | MANTER | médio |
| AG-08 | `compression.idle_compact_floor_tokens` (piso absoluto) + parsing robusto | Hefesto | MANTER | médio |
| AG-09 | Idle compaction respeita o bloqueio automático (anti-thrash/cooldown) | frota | MANTER | baixo |
| AG-10 | Idle compaction arma o checkpoint nativo Responses em vez de resumo local | só Codex/Responses | MANTER (opcional; pode sair por desuso) | baixo |
| AG-11 | Compressor busca de novo o token OAuth Anthropic antes do resumo | Hefesto/frota OAuth | MANTER (em parte redundante) | baixo |
| AG-12 | `compression.summary_policy: authorized_task_v1` (continuidade de tarefa autorizada) | Hefesto | MANTER | médio |
| AG-13 | `agent.pre_verify_on_stop` (o gate de parada roda sem edição de arquivo) | **Hefesto (aceite independente)** | MANTER | **alto** |
| AG-14 | Turno final entregue (com rodapés e hooks) vira a linha durável do assistente | frota | MANTER | médio |
| AG-15 | `gateway_event_context` repassado ao hook `pre_llm_call` | Hefesto (plugins) | MANTER (depende do delta gateway) | médio |
| AG-16 | `skills.selection_policy` (broad / task_relevant) + docs | Hefesto | MANTER (conflito de doc resolvido) | baixo |
| AG-17 | Data URL de visão usa o MIME detectado nos bytes | frota | MANTER | baixo |
| AG-18 | Codex app-server: janela do provedor limitada pelo `context_length` configurado | só Codex | MANTER (opcional; pode sair por desuso) | baixo |
| AG-19 | Codex app-server session: `turn_timeout=None` + `inactivity_timeout` | Codex/plugin? | INCERTO (manter até checar os plugins) | baixo |
| AG-20 | `tool_executor.py`: imports duplicados | ninguém | REMOVER (conflito resolvido = v0.22.5) | baixo |
| AG-21 | default `updates.sync_named_profiles: True` | Arconte (host dono da frota) | MANTER (o consumidor está fora deste escopo) | baixo |

Contagem: **MANTER 19** (inclui AG-10 e AG-18, que podem sair por desuso de Codex), **REMOVER 1** (AG-20),
**INCERTO 1** (AG-19), **ADAPTAR 0**. Nenhuma mudança precisa de adaptação de código para a v0.22.5. A semântica foi
conferida item a item nos pontos em que o upstream mexeu no código ao redor.

---

## AG-01 — Pool mantém o prazo 429 conhecido em retry sem reset

- **Arquivos/funções:** `agent/credential_pool.py::CredentialPool._mark_exhausted` (bloco `previous_reset`).
- **Testes Dize:** nenhum no fork. Probe: `test_429_without_reset_keeps_known_deadline`. Passa na mesclada e falha
  na v0.22.5 pura.
- **Motivo/comportamento:** o commit 3336def46a fala em "keep a known 429 quota deadline when a retry ... reports a
  429 without reset metadata". Sem a mudança, o segundo 429 (corpo sem `reset_at`) grava
  `last_error_reset_at=None`. `_exhausted_until` cai então no TTL genérico de `last_status_at + _exhausted_ttl`,
  que é mais curto, e a conta OAuth Claude volta à rotação antes da janela de cota acabar. O pool fica em ciclo de
  429 e o Hefesto troca de conta à toa.
- **Quem precisa:** frota. É crítico no Hefesto (pool OAuth Anthropic, Opus/Sonnet).
- **v0.22.5:** MANTER. O upstream só mudou `_load_config_safe` e a busca de pool custom. `_mark_exhausted`,
  `_reset_cleared_after`, `_parse_absolute_timestamp` e `STATUS_EXHAUSTED` ficaram iguais. O hunk aplicou limpo, na
  mesma posição.
- **Teste comportamental:** o probe acima. Em produção, depois de um 429 com `anthropic-ratelimit-*-reset`, rodar
  `hermes auth list anthropic` duas vezes e conferir que o "available at" não encurta depois de um segundo 429.
- **Risco:** alto se a mudança se perder (dreno de contas). A aplicação em si tem risco baixo.

## AG-02 — `token_is_blocked` considera credencial esgotada ou morta

- **Arquivos/funções:** `agent/credential_pool_model_cooldowns.py::CredentialPoolModelCooldownMixin.token_is_blocked`.
- **Testes Dize:** stub em `tests/agent/test_auxiliary_client.py` (`token_is_blocked`). Probe:
  `test_token_is_blocked_for_exhausted_and_dead`. Passa na mesclada e falha na pura.
- **Motivo:** no upstream, `token_is_blocked` só olha `model_cooldowns`. Um token entregue fora do `select()` (por
  env, emprestado, cliente aux já construído ou o compressor) seguia em uso mesmo com a credencial `exhausted`
  (429 de conta inteira) ou `dead` (OAuth revogado). O commit 3336def46a diz "treat exhausted or dead credentials
  as unavailable for model cooldown checks".
- **Quem precisa:** frota (Hefesto).
- **v0.22.5:** MANTER. O upstream mexeu no mesmo arquivo: entrou `pool_failure_reason`/`FAILURE_REASON_USAGE_WINDOW`
  e `_is_model_scoped_failure` passou a aceitar `usage_window`. `token_is_blocked` ficou igual. A versão Dize continua
  certa: um cooldown `usage_window` é por modelo e cai em `model_cooldown_until`, que já está incluído. Ela usa
  `_exhausted_until(entry, sole_credential=...)` e `_is_sole_credential()`, que existem na v0.22.5 com a mesma
  assinatura (`credential_pool.py:442` e `:1059`).
- **Atenção:** `resolve_anthropic_token(model=...)` → `_available_anthropic_token` usa o pool. Com o AG-02, um token
  de conta esgotada resolve como `None` para quem passa `model`. É o efeito desejado, mas mais chamadas podem cair
  em "No Anthropic credentials". Ver a memória "Hermes: cooldown de modelo trava pool", cujo conserto é
  `auth reset anthropic`.
- **Teste:** o probe; mais `tests/agent/test_anthropic_model_cooldowns.py` e `test_credential_pool*.py`, que passam
  com o delta.
- **Risco:** alto (lógica de disponibilidade das contas).

## AG-03 — Merge de disco do auth não encurta o prazo 429

- **Arquivos/funções:** `hermes_cli/auth.py::_merge_disk_cooldown_state` (bloco `disk_reset`/`mem_cleared`).
- **Testes Dize:** nenhum. Probe: `test_disk_merge_keeps_provider_deadline`. Passa na mesclada e falha na pura.
- **Motivo:** uma instância velha (outro processo ou perfil) grava um 429 mais novo sem `reset_at` e, ao reescrever
  `auth.json`, apaga o prazo que outra instância tinha recebido do provedor. É o caso "stale instance" do commit
  3336def46a.
- **Quem precisa:** frota. No Hefesto há vários processos e perfis lendo o mesmo pool.
- **v0.22.5:** MANTER. O upstream mudou `auth.py` em outros pontos (`note_store_write` em `_store_provider_state` e
  o texto de `_lock_holder_hint`). `_merge_disk_cooldown_state` ficou igual e o hunk aplicou limpo.
- **Teste:** o probe. Outro teste possível: dois `load_pool("anthropic")` em processos separados, um marca 429 com
  reset, o outro marca 429 sem reset e chama `write_credential_pool`; reler `auth.json` e conferir que o reset
  continua lá.
- **Risco:** alto se a mudança se perder.

## AG-04 — Cliente aux Anthropic escolhe por modelo e recusa token bloqueado

- **Arquivos/funções (`agent/auxiliary_client.py`):** `_select_pool_entry(model=)`; `_anthropic_token_is_blocked`
  (novo); `_try_anthropic(model=)`, que resolve o modelo antes do `select` e devolve `(None, None)` para token
  bloqueado; `_route_client`, que recusa cliente com token bloqueado; `_get_cached_client`, que tira do cache o
  cliente cujo token entrou em cooldown sem fechar o transporte de quem ainda o usa; `_resolve_api_key_branch`, que
  passa `req.model`; `_STRICT_VISION_BACKENDS["anthropic"]`, que passa `model`.
- **Testes Dize:** ajuste em `tests/agent/test_auxiliary_client.py::TestAnthropicOAuthFlag` (o stub ganha
  `select(*, model=None)` e `token_is_blocked`). Probe: `test_try_anthropic_refuses_blocked_token`.
- **Motivo:** tarefas aux (compressão, títulos, visão, aprovação) do Hefesto rodam em Anthropic OAuth. Sem isso,
  `select()` sem modelo ignora o cooldown por modelo, e um cliente montado antes do cooldown continua no cache
  mandando requisições à conta bloqueada. Proxies não-Anthropic são ignorados de propósito (outra autoridade de
  cota).
- **Quem precisa:** frota (Hefesto).
- **v0.22.5:** MANTER. Houve conflito, resolvido em `resolved/agent/auxiliary_client.py`. O upstream reescreveu partes
  vizinhas:
  - `_get_cached_client` passou a usar `pool_for_aux_provider(...)` e `_peek_pool_entry(provider, _pool)` depois do
    cache. O bloco Dize de despejo vem antes e não interfere.
  - `_select_pool_entry` e `_try_anthropic` não mudaram no upstream. `CredentialPool.select(*, model=None)` existe
    (`credential_pool.py:1960`).
  - `_managed_local_*` foi para `agent/auxiliary_managed_local.py` e há novo progresso de prompt local. Nada disso
    toca o caminho Anthropic.
  - A semântica continua certa. Conferi que o diff `v0.22.5 → resolvido` é idêntico, linha a linha, ao diff
    `v0.22.1 → fork` deste arquivo ("DELTA_EQUIVALENT").
- **Custo:** `_anthropic_token_is_blocked` relê o pool (auth.json) a cada acerto de cache e a cada rota Anthropic.
  Já é assim em produção hoje.
- **Teste:** o probe, e `tests/agent/test_auxiliary_client*.py` (passa). Em produção: forçar um cooldown
  (`hermes auth` marcando 429 num modelo) e conferir que a compactação usa outra conta ou o fallback, sem 429
  repetido no log.
- **Risco:** alto (caminho da compactação do Hefesto).

## AG-05 — Aux Anthropic em cooldown cai para a cadeia principal de fallback

- **Arquivos/funções:** `_try_configured_fallback_for_unavailable_client(failed_model=)`, que com
  `pool.next_available_at(model)` no futuro chama `_try_main_fallback_chain(reason="credential cooldown")`;
  `_resolve_call_client`, que passa `failed_model=resolved_model`; `_ladder_provider_fallback`
  (`anthropic_cooldown`); `_next_fallback_after_quarantine(allow_main_chain=)`.
- **Testes Dize:** nenhum. Probe: `test_unavailable_anthropic_in_cooldown_uses_main_chain`.
- **Motivo:** com provedor aux explícito `anthropic`, o upstream para na cadeia da tarefa ("the main-agent model
  stays the runtime last resort"). Uma conta configurada mas em quota não é "falta de login", então a Dize deixa usar
  o `fallback_providers` principal. Sem isso, a compressão levanta `AuxiliaryClientUnavailable` enquanto todas as
  contas estão em cooldown.
- **Quem precisa:** frota (Hefesto).
- **v0.22.5:** MANTER. **Conflito** no fim de `_ladder_provider_fallback`. O upstream reescreveu o comentário e
  passou a guardar/restaurar `primary_route`; a Dize acrescenta `allow_main_chain=anthropic_cooldown`. A resolução
  mantém o código e o comentário do upstream e acrescenta só o argumento Dize. `_try_main_fallback_chain(task,
  failed_provider, reason, *, failed_model, failed_base_url, failure_scope)` tem a mesma assinatura na v0.22.5. A
  restauração de `primary_route` do upstream continua valendo para os candidatos Dize, porque eles passam pelo mesmo
  `while`.
- **Teste:** o probe. Integração: pool com uma conta, 429 por modelo com reset de 10 min e `fallback_providers`
  apontando para um stub; chamar `call_llm(task="compression")` e conferir que o stub foi chamado.
- **Risco:** médio.

## AG-06 — Erro estruturado na rotação aux; sem retry na mesma chave com reset futuro

- **Arquivos/funções:** `_recover_provider_pool._rotate`, que usa `extract_api_error_context(exc)` em vez de
  `{"message": str(exc)}`; `_ladder_credential_rungs`, que não repete a chamada com a mesma chave quando o erro traz
  `reset_at` futuro.
- **Testes Dize:** nenhum direto.
- **Motivo:** sem o contexto estruturado, a rotação aux não passa `reset_at` ao pool, e o AG-01 e o AG-03 não têm o
  que preservar. O retry na mesma chave contra uma janela de cota conhecida só gasta uma requisição e gera outro 429.
- **Quem precisa:** frota.
- **v0.22.5:** MANTER. `extract_api_error_context` é idêntico nas duas versões (sempre devolve `message` e lê
  `reset_at`/headers), e `agent.credential_pool._parse_absolute_timestamp` continua sendo um nome importado no
  módulo.
- **Teste:** mock de `APIStatusError(429)` com header `anthropic-ratelimit-requests-reset` no futuro; conferir que o
  aux não faz a segunda chamada na mesma chave e que o pool grava `last_error_reset_at`.
- **Risco:** médio.

## AG-07 — Idle compaction pelo relógio durável

- **Arquivos/funções:** `agent/turn_context.py::_idle_gap_since_durable_activity` (novo);
  `agent/turn_context_compaction.py::_idle_compaction`, que usa essa função.
- **Testes Dize:** `tests/agent/test_idle_compaction.py` (2 testes novos) e `test_idle_compaction_lock_and_guards.py`
  (que faz patch dessa função).
- **Motivo:** o gateway zera `agent._last_activity_ts` a cada turno externo (`gateway/run.py::_init_cached_agent_for_turn`,
  v0.22.5 `:4152`). Com isso o idle compaction nunca disparava no gateway. A correção lê
  `sessions.last_activity_at`.
- **Quem precisa:** Hefesto (gateway com sessões longas).
- **v0.22.5:** MANTER. O reset direto continua igual na v0.22.5, e `touch_session_activity` continua sem andar para
  trás.
- **Ponto não provado (vale para as duas versões):** os testes fazem patch da própria função. Nenhum teste prova,
  ponta a ponta, que nada grava `last_activity_at` entre a chegada da mensagem e `build_turn_context`. Por exemplo, o
  `_touch_activity` da hygiene compression faz uma escrita durável quando está "due". Se gravar, o gap fica perto de
  zero e o idle não dispara. Isso não é regressão da atualização.
- **Teste:** procurar "Idle compaction:" no log do Hefesto depois de uma sessão ficar ociosa mais que
  `idle_compact_after_seconds` e receber uma mensagem nova.
- **Risco:** médio (perder a mudança deixa o idle inerte; aplicar é seguro).

## AG-08 — Piso absoluto `compression.idle_compact_floor_tokens`

- **Arquivos/funções:** `agent/agent_init.py::_parse_compression_config` (lê o piso; também deixa
  `idle_compact_after_seconds` robusto a bool e string), `CompressionSettings.idle_compact_floor_tokens`,
  `_build_context_engine` (`agent.compression_idle_compact_floor_tokens`), `_idle_compaction` (usa o piso),
  `hermes_cli/config_defaults.py` (`compression.idle_compact_floor_tokens: None`), `cli-config.yaml.example`.
- **Testes Dize:** `test_idle_compaction.py::test_does_not_fire_at_explicit_conservative_floor` e
  `test_idle_compaction_lock_and_guards.py::test_idle_compaction_respects_explicit_token_floor`. Probe:
  `test_idle_floor_parse` (6 casos).
- **Motivo:** uma hora ociosa dava permissão para resumir uma thread de 90K. O piso adaptativo
  (`threshold*target_ratio`) é baixo demais para o Hefesto, cujo config usa 300000.
- **v0.22.5:** MANTER. Ponto delicado: **a dataclass `CompressionSettings` precisa do campo
  `idle_compact_floor_tokens`**. Na mesclada ele existe e o probe passa. Na v0.22.5 o upstream ainda faz
  `int(cfg.get("idle_compact_after_seconds"))` sem tratar bool. Os hunks aplicaram limpo apesar das 55 linhas que o
  upstream mudou em `agent_init.py`.
- **Teste:** o probe, mais um teste de integração com sessão de 299.999 tokens ociosa por 2h (não compacta) e outra
  de 300.001 (compacta).
- **Risco:** médio (sem o piso, o Hefesto voltaria a compactar threads pequenas depois de ociosidade).

## AG-09 — Idle respeita o bloqueio automático (anti-thrash)

- **Arquivos/funções:** `_idle_compaction`, que chama `type(_compressor)._automatic_compression_blocked`.
- **Testes Dize:** `test_idle_compaction_lock_and_guards.py::test_idle_compaction_respects_anti_thrash_breaker`
  (estendido).
- **Motivo:** o idle não pode furar o breaker de compactação inefetiva nem o cooldown de falha. O commit 80ad153c72
  fala em "guard idle context compaction".
- **v0.22.5:** MANTER. `_automatic_compression_blocked(self, *, ignore_cooldown=False)` existe em
  `context_compressor.py:3047` na v0.22.5.
- **Risco:** baixo.

## AG-10 — Idle arma o checkpoint nativo (Responses) em vez de resumo local

- **Arquivos/funções:** `agent/native_compaction.py::native_compaction_eligible_for_agent` (novo);
  `native_compaction_context_management`, que considera `_codex_responses_idle_compact_threshold_for_turn`; o reset
  por turno em `_idle_compaction`.
- **Testes Dize:** `tests/agent/test_native_compaction.py` (6 novos) e
  `test_idle_compaction_lock_and_guards.py::test_idle_compaction_arms_native_checkpoint_instead_of_local_summary`.
- **Motivo:** em `api_mode=codex_responses` é melhor pedir ao servidor um checkpoint nativo do que fazer o resumo
  local, que perde informação.
- **Quem precisa:** só agentes Codex/Responses. **O Hefesto (Anthropic) nunca é elegível.**
  `native_compaction_eligible_for_agent` devolve False para `anthropic_messages`.
- **v0.22.5:** MANTER, opcional. `native_compaction.py` não mudou no upstream. Pode sair por desuso, desde que o
  AG-08 e o AG-09 fiquem e a linha `agent._codex_responses_idle_compact_threshold_for_turn = None` também seja
  retirada. Nenhum plugin listado usa estes símbolos.
- **Risco:** baixo.

## AG-11 — Compressor busca de novo o token OAuth Anthropic antes do resumo

- **Arquivos/funções:** `agent/context_compressor.py::ContextCompressor._fresh_main_api_key` (novo), usado em
  `_call_summary_llm`.
- **Testes Dize:** nenhum. Probe: `test_compressor_reresolves_oauth`.
- **Motivo:** o commit a98b934972 diz "re-resolve a rotating native Anthropic OAuth token before summary calls,
  so compaction stops failing with 401 after the main agent refreshes it".
- **v0.22.5:** MANTER. **É redundante em parte:** desde a v0.22.1 o upstream tem
  `conversation_compression._refresh_main_credential(agent)`, chamada em `compress_context` (v0.22.5 `:3960/:4066`).
  Ela chama `client_lifecycle._try_refresh_anthropic_client_credentials`, que também move `cc.api_key`
  (`client_lifecycle.py:924`). A versão Dize ainda cobre quem chama `_call_summary_llm` sem passar por
  `compress_context` com um agente vivo (por exemplo, um compressor isolado ou hygiene). Também pega o caso em que a
  troca aconteceu em outro processo. O custo é uma resolução de token por resumo. Remover só depois de confirmar nos
  logs do Hefesto que não há mais "OAuth access token has been revoked" vindo da compressão.
- **Efeito colateral:** com pool de várias contas, `resolve_anthropic_token(model=)` pode devolver outra conta, ou
  `None` se a conta estiver bloqueada (nesse caso mantém a chave antiga). Isso é aceitável para uma tarefa aux.
- **Risco:** baixo.

## AG-12 — Continuidade de tarefa autorizada (`compression.summary_policy: authorized_task_v1`)

- **Arquivos/funções:** `agent/context_compressor.py`: `AUTHORIZED_TASK_SUMMARY_PREFIX`; `__init__`, que lê
  `compression.summary_policy` com `load_config_readonly()` e, se ativo, faz
  `self.select_context = self._select_authorized_task_context`; `_replace_summary_envelope`;
  `_select_authorized_task_context`.
- **Testes Dize:** `tests/agent/test_authorized_task_continuity.py` (15 testes; passa na mesclada).
- **Motivo:** depois de uma compactação, o envelope padrão faz o modelo tratar o resumo como contexto passivo e às
  vezes descartar a tarefa autorizada pendente. A política reescreve **só na cópia que vai para o fio** o prefixo
  dos resumos comprovados por metadado. Não reescreve o histórico persistido e recusa duplicatas ambíguas e
  citações forjadas.
- **v0.22.5:** MANTER. As dependências seguem iguais:
  - `_apply_context_engine_selection` é idêntico nas duas versões e ainda é chamado de
    `turn_request_assembly.py:145`;
  - `_engine_overrides_hook` compara `hook.__func__` com `ContextEngine.select_context`, e o método ligado da
    instância passa;
  - `_has_compressed_summary_metadata`, `_HISTORICAL_SUMMARY_PREFIXES`, `_MERGED_SUMMARY_DELIMITER`,
    `SUMMARY_PREFIX` e `LEGACY_SUMMARY_PREFIX` não mudaram.

  O que a v0.22.5 traz de novo ao redor: `mark_inflight_restatement(replay)` e o `ProactivePruneMixin`. Nenhum dos
  dois toca a projeção, e os 15 testes passam.
- **Atenção multiplex:** a política é lida por perfil via `load_config_readonly()`. O teste
  `test_real_profile_config_a_b_a_scopes_selection_without_global_leak` prova o isolamento. É relevante porque na
  v0.22.5 `gateway.multiplex_profiles: false` está "retired" (ver gateway).
- **Teste:** os 15 testes; em produção, abrir `request_dump` de um turno pós-compactação do Hefesto e conferir o
  prefixo `[CONTEXT COMPACTION — AUTHORIZED TASK CONTINUITY V1]`.
- **Risco:** médio (afeta o comportamento pós-compactação, que é o uso real do Hefesto).

## AG-13 — `agent.pre_verify_on_stop` (gate de parada sem edição de arquivo)

- **Arquivos/funções:** `agent/turn_stop_gates.py::_pre_verify_nudge`.
- **Testes Dize:** **nenhum no fork.** Probe: `test_pre_verify_on_stop_opt_in` (True dispara; False e a string
  `"true"` não). Na v0.22.5 pura o caso True falha.
- **Motivo:** no upstream o hook `pre_verify` só roda depois de edição de arquivo (`_turn_file_mutation_paths`). O
  aceite independente do Hefesto (plugin) precisa conferir trabalho que não é arquivo (delegação, publicação,
  snapshot de projeto). Com `agent.pre_verify_on_stop: true` (exatamente o bool `True`), o hook roda em toda parada
  de texto e respeita `max_verify_nudges()` e a alternância de papéis.
- **Quem precisa:** **só o Hefesto, e é crítico.**
- **v0.22.5:** MANTER. O upstream pôs um gate novo **antes**, `_late_lsp_verdicts`, que só age quando há veredito de
  LSP pendente sobre arquivos editados. Depois dele a ordem segue verify-on-stop → pre_verify → kanban.
  `get_pre_verify_continue_message(...)`, `max_verify_nudges`, `has_hook` e `_pre_verify_synthetic` (em
  `_SYNTHETIC_USER_FLAGS`) não mudaram. `apply_stop_gates` continua chamado em `turn_final_response.py:332` e esse
  arquivo não mudou.
- **Observação para o plugin:** a nota de LSP nova é uma linha `user` durável, **sem flag sintética**. O
  `conversation_compression._is_real_user_message`, que o plugin importa, vai tratá-la como mensagem humana real.
  Só acontece em turnos com LSP ativo.
- **Teste:** o probe, mais um ponta a ponta: perfil com `agent.pre_verify_on_stop: true` e plugin de teste com hook
  `pre_verify` que devolve `{"action":"continue","message":"X"}` na tentativa 0. Um turno sem edição deve terminar
  com `finish_reason="verify_hook_continue"` numa resposta intermediária e depois na resposta final.
- **Risco:** **alto.** Sem isso o aceite independente do Hefesto deixa de rodar sem nenhum erro visível.

## AG-14 — Resposta entregue vira a linha durável do assistente

- **Arquivos/funções:** `agent/turn_finalizer.py::finalize_turn`, bloco "Delivery footers and output hooks ...".
- **Testes Dize:** `tests/agent/test_turn_finalizer_final_response_persistence.py::test_transformed_response_is_the_durable_assistant_turn`;
  o ajuste em `test_transform_llm_output_persistence.py` (a linha final agora inclui o aviso de saída anormal que o
  usuário viu).
- **Motivo:** o rodapé de mutação de arquivo, `_explain_abnormal_exit` e `_apply_output_hooks` (`transform_llm_output`)
  rodam **depois** do `_persist_step`. O texto entregue ao usuário divergia do que ficava no state.db. Num restart ou
  resume o modelo via outra resposta, e plugins que leem a transcrição (aceite, Honcho) viam o rascunho.
- **Quem precisa:** frota.
- **v0.22.5:** MANTER. O upstream só mudou `_drain_leftover_steer` no fim da função. A ordem
  rodapé → explicação → hooks → (sync Dize) → `on_turn_complete` continua igual. O teste upstream ajustado pela Dize
  passa na mesclada.
- **Teste:** os testes citados (passam); em produção, comparar a última mensagem entregue no Telegram com a última
  linha `assistant` da sessão no state.db.
- **Risco:** médio (uma escrita extra de sessão por turno transformado).

## AG-15 — `gateway_event_context` no hook `pre_llm_call`

- **Arquivos/funções:** `agent/turn_context.py::_collect_pre_llm_call_context`.
- **Testes Dize:** `tests/gateway/test_dize_automation_delegation_metadata.py` (escopo gateway).
- **Motivo:** os plugins do Hefesto leem metadado de automação/delegação do evento do gateway. O produtor está em
  `gateway/run_turn_runner.py` (`agent._gateway_event_context = ...`) e em `gateway/turn_context.py`
  (`TurnContext.gateway_event_context`), **fora deste escopo**. A v0.22.5 não tem `gateway_event_context` em lugar
  nenhum.
- **v0.22.5:** MANTER. Aplicou limpo. **Depende do delta gateway:** sem o produtor, o hook recebe `{}`, sem erro.
- **Teste:** um plugin de teste com `pre_llm_call(**kw)` grava `kw["gateway_event_context"]`; mandar uma mensagem de
  automação pelo gateway e conferir as chaves.
- **Risco:** médio (o plugin perde o metadado sem erro se o lado gateway não vier junto).

## AG-16 — Política de seleção de skills (`skills.selection_policy`)

- **Arquivos/funções:** `agent/prompt_builder.py::_render_skills_index(selection_policy=)` e
  `_build_skills_system_prompt_inner` (lê a config, valida, inclui na chave de cache);
  `hermes_cli/config_defaults.py` (`skills.selection_policy: "broad"`); `website/docs/user-guide/features/skills.md`.
- **Testes Dize:** `tests/agent/test_skill_selection_policy.py` (passa).
- **Motivo:** o texto `broad` do upstream manda carregar qualquer skill "parcialmente relevante". O Hefesto usa
  `task_relevant` para reduzir carregamentos tangenciais sem esconder skills (commit 6b1de66c97).
- **v0.22.5:** MANTER. As 120 linhas que o upstream tirou de `prompt_builder.py` são `PLATFORM_HINTS` e notas de
  mídia, não o bloco de skills. `_render_skills_index` continua com o mesmo corpo. O ramo de sessão one-shot
  (`ONESHOT_SKILLS_LOAD_GUIDANCE`) continua antes do ramo Dize, como antes.
- **Conflito em docs (resolvido):** `resolved/website/docs/user-guide/features/skills.md`. A frase do upstream mudou
  o exemplo de `official/migration/openclaw-migration` para `official/mcp/fastmcp`. A resolução fica com a frase do
  upstream e acrescenta a seção Dize "## Skill selection policy" logo depois, sem alterar o texto dela.
- **Diferença de comportamento (proposital):** o texto `task_relevant` não traz a frase do upstream "If a skill you
  loaded was missing steps ... update it before finishing".
- **Teste:** os 5 testes; em produção, o system prompt de uma sessão nova do Hefesto deve começar a seção de skills
  com "Review the skill names and descriptions below".
- **Risco:** baixo.

## AG-17 — MIME de visão detectado nos bytes

- **Arquivos/funções:** `tools/vision_tools.py::_image_to_base64_data_url`, que usa
  `agent.image_routing._sniff_mime_from_bytes`.
- **Testes Dize:** nenhum. Probe: `test_vision_data_url_sniffs_jpeg_named_png`. Passa na mesclada e falha na pura.
- **Motivo:** "Anthropic rejects a JPEG labelled image/png (HTTP 400)" (commits a98b934972 e 3336def46a). Na v0.22.5
  o `mime_type` ainda vem do chamador (`prepared.mime`) ou da extensão (`_determine_mime_type`, cujo padrão é jpeg),
  sem conferir os bytes.
- **v0.22.5:** MANTER. O upstream só trocou `mkdir` por `make_user_dir`. `_sniff_mime_from_bytes` existe
  (`image_routing.py:419`). O SVG continua excluído de propósito.
- **Teste:** o probe; `tests/tools/test_vision_tools.py` passa.
- **Risco:** baixo.

## AG-18 — Codex app-server: janela do provedor limitada pelo config

- **Arquivos/funções:** `agent/codex_runtime.py::_record_codex_app_server_usage`.
- **Testes Dize:** `tests/agent/test_codex_app_server_integration.py::test_codex_app_server_context_length_config_is_a_cap`
  (7 parâmetros; passa).
- **Motivo:** o upstream sobrescrevia `compressor.context_length` com `turn.model_context_window` (por exemplo 372K),
  ignorando `model.context_length` do operador e sem recalcular os limiares. A Dize usa `update_model(...)` com
  `min(janela, teto)`.
- **Quem precisa:** só o runtime Codex app-server, **desativado no Hefesto**.
- **v0.22.5:** MANTER, opcional. A única mudança do upstream no arquivo é
  `from agent.conversation_compression_checkpoint import _checkpoint_blocked`, em outra função. A assinatura de
  `update_model` é compatível (o teste passa). Pode sair por desuso: não muda nenhuma assinatura que os plugins
  importam de `agent.codex_runtime`.
- **Risco:** baixo.

## AG-19 — Codex app-server session: `turn_timeout` opcional e `inactivity_timeout`

- **Arquivos/funções:** `agent/transports/codex_app_server_session.py`: `run_turn(turn_timeout: Optional, inactivity_timeout=None)`
  (valida valores finitos e positivos), `_run_started_turn` (rastreia atividade e ferramentas ativas e interrompe por
  inatividade só sem ferramenta ativa), `_drive_turn` (deadline opcional).
- **Testes Dize:** `tests/agent/transports/test_codex_app_server_session.py` (2 novos mais um ajuste de relógio;
  passa).
- **Motivo:** permite turnos Codex longos sem prazo total, aposentando a sessão por inatividade real.
- **Quem chama:** **nenhum chamador no código** passa `inactivity_timeout` ou `turn_timeout=None`. O único consumidor
  possível é um plugin de perfil, já que os plugins importam `agent.transports.codex_app_server`. Não tenho acesso aos
  plugins do Hefesto. O plugin local do Arconte (`dize-project-delegation`) não usa.
- **v0.22.5:** INCERTO. O upstream não mexeu neste arquivo e aplicou limpo. Recomendo **manter**, porque o custo é
  baixo e a remoção quebraria com `TypeError` um plugin que passe `inactivity_timeout`. Antes de remover, rodar no
  Hefesto `grep -rn "inactivity_timeout\|turn_timeout=None\|run_turn(" ~/.hermes/profiles/hefesto/plugins`.
- **Risco:** baixo.

## AG-20 — `agent/tool_executor.py`: imports duplicados (REMOVER)

- **Delta Dize:** acrescenta um segundo `from agent.compression_marker import _COMPRESSION_MARKER_PREFIX` e repete
  `_context_pruned_argument_paths` na lista de `tool_dispatch_helpers`. **v0.22.1 já importava os dois** (`:33` e
  `:51`), então não muda comportamento. Parece resto de um backport achatado.
- **v0.22.5:** REMOVER. A v0.22.5 importa os dois (`tool_executor.py:33` e `:53`). O conflito veio do upstream ter
  acrescentado `from agent.message_metadata import stamp_data_use` na mesma linha.
- **Resolução:** `resolved/agent/tool_executor.py` = **v0.22.5 sem alterações**. Atenção: a árvore de trabalho do probe
  também ganhou um `_context_pruned_argument_paths` duplicado fora dos marcadores. A versão resolvida o elimina.
- **Teste:** `python -c "import agent.tool_executor"`, mais os testes que citam `tool_executor`.
- **Risco:** baixo.

## AG-21 — Default `updates.sync_named_profiles: True`

- **Arquivos:** `hermes_cli/config_defaults.py`. O consumidor é `hermes_cli/update_cmd_maint.py::_sync_named_profiles_on_update_enabled`,
  fora deste escopo; o teste é `tests/hermes_cli/test_update_named_profile_scope.py`.
- **Motivo:** um host dono da frota (Arconte) mantém perfis nomeados como projeções somente leitura. `false` impede
  que `hermes update` faça seed de skills ou backfill de credenciais nesses perfis. O default `True` mantém o
  comportamento do upstream.
- **v0.22.5:** MANTER. Aplicou limpo.
- **Risco:** baixo para o Hefesto.

---

## Arquivos em conflito do escopo — resoluções candidatas

| Arquivo | Resolução | Caminho |
|---|---|---|
| `agent/auxiliary_client.py` | Código e comentário do upstream mais `allow_main_chain=anthropic_cooldown` (AG-05). O diff da versão resolvida contra a v0.22.5 é idêntico ao diff do fork contra a v0.22.1. | `scratchpad/resolved/agent/auxiliary_client.py` |
| `agent/tool_executor.py` | v0.22.5 sem alterações (AG-20); o delta Dize só tinha imports duplicados | `scratchpad/resolved/agent/tool_executor.py` |
| `website/docs/user-guide/features/skills.md` | Frase do upstream (`official/mcp/fastmcp`) mais a seção Dize "Skill selection policy" sem alterações | `scratchpad/resolved/website/docs/user-guide/features/skills.md` |

Nenhum dos três tem marcador. A árvore `wt-agent` com eles importa e passa nos testes acima.

## Símbolos internos usados pelos plugins (v0.22.1 → v0.22.5)

| Símbolo | Assinatura | Corpo | Observação |
|---|---|---|---|
| `agent.conversation_compression._raise_if_stale_attempt(compressor)` | igual | idêntico | depende de `_caller_attempt_is_current` (existe) |
| `agent.conversation_compression._is_real_user_message(message)` | igual | idêntico | `_SYNTHETIC_USER_FLAGS`/`_PREFIXES` iguais; **a nova nota de LSP da v0.22.5 conta como mensagem humana real** |
| `agent.turn_context.compose_user_api_content(content, ext_prefetch_cache, plugin_user_context)` | igual | idêntico | — |
| `agent.turn_context.drop_stale_api_content(msg)` | igual | idêntico | — |
| `agent.agent_runtime_helpers.strip_think_blocks(agent, content)` | igual | idêntico | — |
| `agent.subagent_lifecycle.get_active_subagent_parent()` | igual | idêntico | arquivo sem mudança |
| `agent.redact.redact_sensitive_text(text, *, force, code_file, file_read, secret_file, redact_url_credentials)` | igual | idêntico | o módulo só ganhou constantes de profundidade |
| `agent.transports.codex_app_server` (módulo) | — | arquivo sem mudança | — |
| `agent.codex_runtime` (módulo) | — | só troca do import de `_checkpoint_blocked` | `_checkpoint_blocked` foi para `agent.conversation_compression_checkpoint`, mas continua importável de `agent.conversation_compression` (re-export na linha 1378) |
| `agent.turn_stop_gates` / hook `pre_verify` | `get_pre_verify_continue_message` igual | — | ver AG-13 |

O plugin Honcho local (Arconte) também importa `agent.memory_manager.sanitize_context`. Na v0.22.5 ele mora em
`agent.memory_context` mas é reexportado por `memory_manager` (`memory_manager.py:25`), então continua funcionando.

## Config: chaves exclusivas do fork

Probe `test_fork_config_keys_survive_load_migrate_save`, rodado na mesclada **e** na v0.22.5 pura:

1. grava `config.yaml` com `_config_version: 51`, `agent.pre_verify_on_stop: true`,
   `compression.summary_policy: authorized_task_v1`, `compression.idle_compact_floor_tokens: 300000`,
   `skills.selection_policy: task_relevant` e `gateway.multiplex_profiles: false`;
2. roda `load_config()`, `migrate_config(interactive=False, quiet=True)`, `save_config(load_config())` e
   `load_config_readonly()`;
3. **todas as chaves sobrevivem nas duas versões.** `multiplex_profiles: false` também sobrevive a esse ciclo
   load/migrate/save.

Fatos que sustentam isso: `_config_version` continua 51 na v0.22.5 (nenhuma migração nova roda) e
`config_migrations.py` não mudou. O merge profundo com `DEFAULT_CONFIG` preserva subchaves desconhecidas.
`_validate_config_key` e `set_config_value` só **recusam** caminhos com prefixo errado ("wrong-prefix"); fora isso,
apenas avisam.

**O que este escopo não cobre:** a v0.22.5 (como já a v0.22.1) tem em `hermes_cli/gateway_multiplex_mode.py` o
`RETIRED_OPT_OUT_REASON`: "gateway.multiplex_profiles: false is retired and was rewritten to true". O próprio gateway
reescreve no boot e deixa o marcador `.multiplex_opt_out_rewritten`. O fork altera esse arquivo (+12/−18), e o caso
fica para o inventário do gateway.

Sugestão opcional (ADAPTAR leve, não obrigatório): pôr `agent.pre_verify_on_stop: False` e
`compression.summary_policy: "default"` no `DEFAULT_CONFIG`, para que `hermes config set` não avise "not a
recognized key" e as opções apareçam em `hermes config show`.

## Outras mudanças upstream relevantes vistas no caminho (não Dize)

- `sessions.auto_prune` mudou de `True` para **`False`** por padrão na v0.22.5 (`config_defaults.py`). Se o Hefesto
  dependia do prune automático sem a chave explícita, o state.db volta a crescer até alguém responder ao
  `prune_advisor`.
- Gate de LSP tardio antes do pre_verify (ver AG-13).
- `vision.replay_recent_images: 3`: imagens recentes são reenviadas em turnos reconstruídos do state.db. Isso aumenta
  o payload em sessões do Hefesto com imagens.

## Varredura ampla

Rodei na árvore mesclada (`wt-agent`) todos os 2.815 testes que citam `turn_stop_gates`, `pre_verify`,
`native_compaction`, `codex_app_server_session`, `skills_system_prompt`/`_render_skills_index`, `select_context`,
`idle_compact`, `token_is_blocked`, `_merge_disk_cooldown_state` ou `tool_executor`:
**2805 passed, 4 failed, 5 skipped, 1 xfailed** (25 min).

As 4 falhas não vêm do delta Dize:

| Teste | Isolado na mesclada | Isolado na v0.22.5 pura |
|---|---|---|
| `tests/agent/test_nt_namespace_guard.py::...test_file_tools_reject_raw_string_without_resolving` | falha | **falha igual** (ambiente Linux) |
| `tests/agent/test_run_agent.py::TestExecuteToolCalls::test_sequential_tool_calls_run_without_delay` | falha | **falha igual** |
| `tests/hermes_cli/test_runtime_provider_resolution.py::test_qwen_oauth_auto_fallthrough_on_auth_failure` | falha | **falha igual** |
| `tests/agent/test_inline_tool_executors_memory_args.py::test_memory_alias_persists_with_content_precedence` | passa | passa (depende da ordem na execução combinada) |

Resumo: nenhuma regressão atribuível ao delta deste escopo. Para refazer:
`cd scratchpad/wt-agent && PYTHONPATH=../pydeps TMPDIR=../tmp /usr/local/lib/hermes-agent/venv/bin/python -m pytest -q -p no:cacheprovider <arquivos>`.
