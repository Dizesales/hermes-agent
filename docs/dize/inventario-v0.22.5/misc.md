# Inventário Dize — escopo "misc" (v0.22.1 → v0.22.5, Hefesto)

Base: `v0.22.1^{commit}` (297552a9) → `fork/hermes-hefesto` (14dc95fb) → alvo `v0.22.5^{commit}` (bf0203a7).
Trabalho só leitura e offline. Fontes: `git diff`, mensagens de commit em `/usr/local/lib/hermes-agent` (a32910dc08, 3336def46a, b81c55f2e9, c4cc5ecec9, dceb5e8817), memória local `hermes-v022-hefesto-cli-local.md`, `scratchpad/cfgkeys.txt` (chaves do config do Hefesto).

**Verificação feita:** worktree próprio `scratchpad/wt-misc` = v0.22.5 + todo o delta Dize que aplicou limpo (106 arquivos copiados da árvore do probe; os 9 arquivos em conflito ficaram na versão v0.22.5) + o teste resolvido abaixo. Rodado com `/root/work/venv314-test` (Py 3.14.5), TMPDIR próprio (já limpo):
- 103 passed: `test_pre_gateway_dispatch` (resolvido), `test_telegram_auth_check`, `test_telegram_callback_auth_fail_closed`, `test_telegram_group_gating`, `test_discord_component_auth`, `test_discord_roles_dm_scope`.
- 269 passed: lifecycle guard (2), multiplex (mode/s6/enroll), `test_update_named_profile_scope`, `test_single_query_async_capability`, `test_single_query_plugin_cli_ref`, `test_run_tests_parallel`, holders/FTS (2), `test_gateway_housekeeping_plugin_hook`, `test_cli_async_delegation_ownership`, gbrain librarian/projector, honcho ingest, `test_dize_single_profile_homes`.
- Node: `outbound_ids.test.mjs` 7/7 ok. `bridge.native.test.mjs` **não rodou** (sem node_modules).

## Tabela-resumo

| id | título | quem precisa | v0.22.5 | conflito | risco p/ Hefesto |
|---|---|---|---|---|---|
| MX-01 | `gateway.multiplex_profiles: false` explícito é respeitado e nunca reescrito | frota (crítico p/ Arconte; Hefesto já protegido também por `gateway.standalone`) | MANTER | não | baixo |
| UPD-01 | `updates.sync_named_profiles: false` desliga a sincronização de perfis no `hermes update` | só Arconte | MANTER | não | baixo (não protege skills do Hefesto, ver c) |
| PLG-01 | hook `gateway_housekeeping_tick` (VALID_HOOKS + chamada no housekeeping) | plugins de perfil Dize (quem registra: INCERTO) | MANTER | não | médio se perdido (tick some sem erro) |
| CLI-01 | dono das delegações assíncronas na CLI (`declare_stateless_channel` no `-q`, `is_compression_ancestor`) | frota | MANTER (efeito INCERTO) | não | nenhum (CLI do Hefesto já quebrada) |
| CLI-02 | correção de closure `e` em `_present_store_warning` (Py 3.14) | frota | MANTER | não | baixo |
| ST-01 | rótulo de holder do state.db sem argv (não vaza segredo) | frota | MANTER | não | baixo |
| CRON-01 | lifecycle guard: script grande é logado e pulado, não recusa o job | Hefesto (código da frota) | MANTER | não | médio se perdido (crons recusados) |
| TG-01 | pareamento do portal sobrevive ao pré-filtro do Telegram (união com allowlist) | frota (incidente Atena; Hefesto usa `allow_from`/`group_allow_from`) | MANTER | não | **alto se perdido** |
| TG-02 | teto de DM do Telegram `dm_allow_from` / `TELEGRAM_DM_ALLOWED_USERS` | Hefesto (usa `telegram.dm_allow_from`) + frota | MANTER (depende de `gateway/config_loader.py`) | não | **alto se perdido** |
| DC-01 | teto de DM do Discord `dm_allow_from` / `DISCORD_DM_ALLOWED_USERS` (+ botões) | frota (Hefesto não usa Discord) | MANTER | não | nenhum |
| WA-01 | bridge WhatsApp (Baileys): recibos `/delivery-status` e `/resolve-whatsapp` | só Atlas-Kael (WhatsApp) | MANTER | não | nenhum |
| GB-01 | plugin `dize-gbrain-librarian` + `scripts/dize_gbrain_library_projector.py` | Arconte (GBrain); perfis que habilitarem | MANTER (só Dize) | não | baixo |
| HON-01 | `scripts/dize_honcho_arconte_ingest.py` | só Arconte | MANTER (só Dize) | não | nenhum |
| TST-01 | isolamento de teste: `run_tests_parallel` tira `HERMES_HOME`; guarda Feishu no conftest | frota/dev | MANTER | não | protege a home real ao rodar testes no host |
| TST-02 | testes de regressão de hook assíncrono `pre_gateway_dispatch` | frota (plugins com hook async) | código já nativo; **testes MANTER** | **sim** (resolvido) | baixo |

Contagem: 15 mudanças; 14 MANTER (+1 em que o código já é nativo e só os testes ficam); 0 REMOVER; 1 conflito no meu escopo (resolvido); INCERTO parcial em CLI-01 (efeito), PLG-01 (quem consome), WA-01 (uso atual).

---

## MX-01 — `multiplex_profiles: false` explícito preservado
1. **Arquivos/funções:** `hermes_cli/gateway_multiplex_mode.py` (docstring do módulo; `host_serves_named_profile`; `default_gateway_multiplexes` → `bool(flag)`; `resolve_multiplex_mode` → `if current is False: return MultiplexDecision(False, "config", "explicit single-profile selection")`). Testes Dize: `tests/hermes_cli/test_gateway_multiplex_mode.py` (`test_explicit_profile_selection_is_preserved`, `test_explicit_single_profile_never_rewrites_or_discovers_other_profiles`), `test_gateway_multiplex_s6.py`, `test_gateway_enroll_multiplex_warning.py`.
2. **Motivo:** a32910dc08 "explicit gateway.multiplex_profiles: false keeps one profile per gateway". Na upstream o `false` é "RETIRED": decide como se não estivesse definido e, se nada bloquear, liga o multiplex e **regrava o config.yaml para `true`** (`persist_resolved_default` + marcador `.multiplex_opt_out_rewritten`).
3. **Quem:** frota. Crítico no Arconte (perfil default com `false`, segundo b81c55f2e9). No Hefesto a decisão já sai antes, por `standalone_launcher_decision` (perfil nomeado + `gateway.standalone: true`), então o patch é uma segunda proteção.
4. **v0.22.5:** MANTER. A upstream continua aposentando o `false`: `RETIRED_OPT_OUT_REASON`, `resolve_multiplex_mode` (`retired_opt_out = current is False` → `MultiplexDecision(True, "retired-opt-out")`), `log_multiplex_decision` → `persist_resolved_default`. O patch aplicou limpo.
5. **Entorno novo (7):** a v0.22.5 traz `gateway.auto_multiplex_migration` e `GatewayProfileReconcileMixin._multiplex_for_added_profile` (`gateway/run_profile_reconcile.py`), que liga o multiplex no meio da execução quando aparece um segundo perfil. Isso só acontece se `recorded_standalone_reason()` for `SINGLE_PROFILE_REASON` ou `AUTO_MIGRATION_OPTED_OUT_REASON`. Com o patch a decisão é `source="config"`, então `multiplex_standalone_reason` fica `None` e a função retorna False. Também exige `launch == default root`, o que não é o caso do Hefesto. Protegido.
6. **Teste pós-update:** `grep multiplex_profiles config.yaml` antes e depois do restart deve ser idêntico. Também: ausência de `.multiplex_opt_out_rewritten`, `hermes gateway status` sem "retired", e no log nenhuma linha `Wrote gateway.multiplex_profiles: true`.
7. **Risco Hefesto:** baixo. Recomendo `gateway.auto_multiplex_migration: false` no perfil default dos hosts com mais de um perfil (proteção a mais contra `maybe_auto_migrate_after_update`, ver (a)).

## UPD-01 — `updates.sync_named_profiles`
1. `hermes_cli/update_cmd_maint.py`: `_sync_profiles_after_update` sai cedo, e há a nova `_sync_named_profiles_on_update_enabled()`. A chave padrão `updates.sync_named_profiles: True` fica em `hermes_cli/config_defaults.py` (fora do meu escopo, aplicou limpo). Teste: `tests/hermes_cli/test_update_named_profile_scope.py`.
2. Docstring: "Fleet-owner hosts can keep named profiles as read-only projections of runtimes owned elsewhere". Serve para o update não mexer em perfis espelhados de outros hosts.
3. **Quem:** só Arconte. O config do Hefesto não tem `updates.*` (cfgkeys), então vale o padrão `True`.
4. **v0.22.5:** MANTER. A upstream mudou outras partes do arquivo (aviso de FTS, `repair_windows_home_acl`), mas não `_sync_profiles_after_update`.
5. **Semântica:** `false` pula **todos** os perfis, inclusive o ativo, e também o backfill de `.env` e a sincronização do Honcho. Não impede a sincronização de skills que o próprio gateway faz no boot (`gateway/run_bootstrap.py::_sync_skills`), nem a do início do chat (`hermes_cli/main.py::_start_chat_background_prefetch`).
6. **Teste:** com `updates.sync_named_profiles: false`, `hermes update` imprime "Named-profile sync skipped".
7. **Risco Hefesto:** baixo, mas não é proteção de skills (ver c).

## PLG-01 — hook `gateway_housekeeping_tick`
1. `hermes_cli/plugins.py` (`VALID_HOOKS += "gateway_housekeeping_tick"`). A chamada `_housekeeping_chore("Plugin tick", invoke_hook, "gateway_housekeeping_tick", ...)` está em `gateway/run.py` (fora do escopo, aplicou limpo). Teste: `tests/gateway/test_gateway_housekeeping_plugin_hook.py`.
2. Introduzido em dceb5e8817: "bounded extension point for local plugins", a cada 10 ticks.
3. **Quem:** plugins de perfil Dize. Qual plugin registra é **INCERTO** (código de perfil não acessível offline); provavelmente plugins do Hefesto com tarefas periódicas.
4. **v0.22.5:** MANTER. `register_hook` com nome desconhecido só avisa ("unknown names warn but are still stored"); o essencial é a chamada no housekeeping.
5. Teste pós-update: log do gateway sem "unknown hook gateway_housekeeping_tick", e o plugin consumidor registrando atividade a cada ~10 ticks.
6. **Risco:** médio se a chamada em `gateway/run.py` se perder (o tick some em silêncio).

## CLI-01 — dono das delegações assíncronas na CLI
1. `hermes_cli/cli_single_query.py` (`declare_stateless_channel()` no `-q` não interativo) e `hermes_state_titles.py` (`is_compression_ancestor` público). Relacionado, fora do escopo: `hermes_cli/cli_process_notifications.py::_owns_async_delegation_event`. Testes: `tests/cli/test_single_query_async_capability.py`, `tests/test_cli_async_delegation_ownership.py`.
2. Uma consulta única não pode prometer entrega depois do único turno, e uma notificação de delegação só é aceita com prova de dono (id da sessão ou linhagem de compressão).
3. **Quem:** frota.
4. **v0.22.5:** MANTER (aplica limpo, testes passam), mas o **efeito é INCERTO**. Na v0.22.x a CLI clássica roda o turno dentro do gateway (`hermes_cli/gateway_session_store.py`; v0.22.5 `cli_single_query.py`: "the turn's plugin tools run in the gateway, and this process loads no plugins (PLG-7)"). Consequências:
   - o contextvar marcado no processo da CLI provavelmente não chega ao turno no gateway;
   - `GatewaySessionStore.__getattr__` não lista `is_compression_ancestor` em `_READS` e devolve uma função que levanta `GatewayClientError`. O código Dize trata isso como "não sou dono" (fail-closed), então só vale casamento direto de id.
5. Ajuste opcional fora do escopo: incluir `is_compression_ancestor` em `_READS` de `hermes_cli/gateway_session_store.py`.
6. **Risco Hefesto:** nenhum na prática (`hermes chat`/`-q` já falham no Hefesto, ver d).

## CLI-02 — closure da exceção em `cli_init_mixin`
1. `hermes_cli/cli_init_mixin.py::_present_store_warning(error=e)`.
2. A closure adiada usava `e` depois do `except`; no Python 3.x `e` é apagado ao sair do bloco, o que gera `NameError` ao mostrar o aviso de store indisponível.
3. Frota. **v0.22.5:** MANTER. A upstream refatorou o arquivo (+190 linhas), mas o trecho segue igual (agora ~linha 437) e o hunk aplicou.
4. Teste: abrir a CLI com state.db inacessível e ver o aviso rich, sem NameError.

## ST-01 — holders do state.db sem argv
1. `hermes_state_holders.py::foreign_state_db_holders`: o rótulo virou `"uninspectable Hermes process"`. Testes: `tests/hermes_state/test_fts_holder_instance_scope.py`, `test_fts_runtime_rebuild.py`.
2. O argv de outro processo pode carregar segredos.
3. Frota. **v0.22.5:** MANTER; arquivo idêntico entre v0.22.1 e v0.22.5.

## CRON-01 — lifecycle guard pula script grande
1. `cron/lifecycle_guard.py`: `_has_nul_padded_text`, `_is_size_only_refusal`, `_contains_unsafe_gateway_action` (script local grande gera log e é pulado; script remoto grande é pulado). Testes: `tests/cron/test_lifecycle_guard_budget.py`, `test_lifecycle_guard_heredoc_walk.py`.
2. 3336def46a: "an oversized script is logged and skipped instead of refusing the whole job". Texto preenchido com NUL continua recusado (fail-closed).
3. Necessário no Hefesto (docstring "Hefesto:"); o código é da frota.
4. **v0.22.5:** MANTER; `cron/lifecycle_guard.py` idêntico entre v0.22.1 e v0.22.5.
5. **Afrouxa uma defesa:** um `hermes gateway restart` dentro de um script maior que 1 MiB, ou além do orçamento de varredura, passa sem ser visto; para script remoto grande não há nem a checagem de NUL. A troca é consciente, mas vale registrar.
6. Teste: `hermes cron create` referenciando um script benigno com mais de 1 MiB deve aceitar, com warning "skipped oversized script" no log.

## TG-01 — pareamento através do pré-filtro do Telegram (incidente Atena)
1. `plugins/platforms/telegram/adapter.py::_is_user_authorized_from_message`:
   - `allow_from`/`group_allow_from` só **concede** (`if user in allowed: authorized = True`), não nega mais sozinho;
   - com allowlist de adapter configurada, a decisão antecipada exige `... and adapter_allow_from is None`;
   - sem env, `authorized = False if adapter_allow_from is not None else True`.

   Testes Dize: `tests/gateway/test_telegram_auth_check.py` (`test_group_static_allowlist_miss_can_be_authorized_by_runner_pairing`, `test_group_dynamic_pairing_revocation_applies_without_static_config_change`), `test_telegram_group_gating.py`, `test_telegram_callback_auth_fail_closed.py`.
2. c4cc5ecec9: aprovações do PairingStore (portal) são uma fonte de autorização em união com a allowlist estática, não ficam abaixo dela como teto. Essa correção se perdeu numa atualização anterior e bloqueou usuários da Atena.
3. **Quem:** frota. Atena com certeza; o Hefesto tem `telegram.allow_from` e `telegram.group_allow_from` (cfgkeys).
4. **v0.22.5:** MANTER. A upstream não tocou nas funções de autorização (mudanças em tópicos, `edit_revision`, picker). A mudança de registro (`allowed_users_env`/`allow_all_env` → `gateway_env.AUTHZ`, com `allowlist_suppresses_pairing: True`) mantém para o Telegram o mesmo comportamento de `_get_unauthorized_dm_behavior` da v0.22.1. Os testes da revogação real passam com o runner da v0.22.5.
5. Teste pós-update (obrigatório):
   - um usuário aprovado pelo portal, **fora** de `group_allow_from`, escreve no grupo e é atendido;
   - revogado no portal, passa a ser ignorado sem mudar o config;
   - rodar `pytest tests/gateway/test_telegram_auth_check.py` na árvore instalada.
6. **Risco:** alto se perdido. Hoje aplicou limpo.

## TG-02 — teto de DM do Telegram (`dm_allow_from`)
1. `plugins/platforms/telegram/adapter.py`: `_telegram_dm_allowed_users()`; checagem no topo de `_is_sender_authorized` (~l.835) e de `_is_user_authorized_from_message`; `TELEGRAM_DM_ALLOWED_USERS` em `_telegram_auth_env_configured`. **Dependência fora do escopo:** `gateway/config_loader.py::_SHARED_KEYS` precisa conter `"dm_allow_from"`, senão `telegram.dm_allow_from` do YAML não chega ao `extra` (confirmado aplicado na árvore do probe, l.220). Testes: `test_dm_specific_allowlist_blocks_private_only_and_preserves_group_users`, `test_dm_specific_allowlist_precedes_runner_pairing_authorization`, `test_config_preserves_telegram_dm_allow_from_separately`.
2. Restringe quem fala com o bot em DM sem mexer nas ACLs de grupo. O teto vem antes até do pareamento.
3. **Quem:** Hefesto (`telegram.dm_allow_from` está no cfgkeys) e a frota.
4. **v0.22.5:** MANTER. A upstream não tem equivalente (`git grep dm_allow_from` na v0.22.5 só acha signal/yuanbao/whatsapp).
5. Semântica no entorno novo: o teto só existe no adapter, porque `authz_mixin._is_user_authorized` não o conhece. Entradas novas da v0.22.5 que não passam pelo pré-filtro:
   - `_handle_forum_topic_created` só registra o id do tópico e nunca vira turno;
   - edições e eventos reenviados da fila de retenção (held) seguem pelo mesmo handler (INCERTO-baixo).
6. Teste: DM de usuário fora de `dm_allow_from` é ignorada; o mesmo usuário num grupo permitido é atendido.
7. **Risco:** alto se perdido (DM aberta a pareados/allowlist geral).

## DC-01 — teto de DM do Discord
1. `plugins/platforms/discord/adapter.py` (`_GATE_ENV_KEYS`, `_dm_allowed_user_ids` no connect, checagem em `_is_allowed_user`, `_apply_yaml_config` → `_gate("dm_allow_from", ...)`) e `adapter_component_auth.py::_component_check_auth`. Testes: `test_discord_roles_dm_scope.py`, `test_discord_component_auth.py`.
2. Mesmo teto de DM aplicado ao Discord, incluindo cliques em botões/componentes.
3. **Quem:** frota, quais hosts usam é INCERTO; o Hefesto não tem `discord` no cfgkeys.
4. **v0.22.5:** MANTER. `_gate_raw` ganhou o parâmetro `config=`, compatível com a chamada Dize.
5. Ressalva: o novo `adapter_authorization.reauthorize_native_roles` (reautorização por cargo ao recuperar entrada durável) não consulta o teto de DM. Risco baixo, porque a entrada original já teria sido barrada na ingestão.

## WA-01 — bridge WhatsApp (Baileys)
1. `scripts/whatsapp-bridge/bridge.js` (`POST /resolve-whatsapp`, `POST /delivery-status`, observa `messages.update` e `message-receipt.update`), `bridge_helpers.js::normalizeWhatsAppLookupCandidates`, `outbound_ids.js` (rastreador com status e `waitFor`), mais os testes `.mjs`.
2. Expõe prova de entrega e consulta de número sem abrir um segundo socket.
3. **Quem:** só Atlas-Kael (WhatsApp). Nenhum consumidor no core (`git grep delivery-status` vazio), então são plugins externos; uso atual INCERTO (a memória diz que o Kael usa Cloud API).
4. **v0.22.5:** MANTER; diretório idêntico entre v0.22.1 e v0.22.5.

## GB-01 — GBrain librarian
1. `plugins/dize-gbrain-librarian/{__init__.py,plugin.yaml}` (hooks `pre_llm_call`/`post_llm_call`, `ctx.call_mcp("gbrain", ...)`), `scripts/dize_gbrain_library_projector.py`. Testes: `tests/plugins/test_dize_gbrain_librarian.py`, `tests/scripts/test_dize_gbrain_library_projector.py`.
2. Busca semântica limitada antes do turno, revisão de captura e projeção de Markdown canônico para o repositório GBrain.
3. **Quem:** Arconte (série "activate GBrain librarian" em `/usr/local/lib/hermes-agent`). Se o Hefesto habilita, é INCERTO (`plugins.enabled` não é visível).
4. **v0.22.5:** MANTER (arquivos só da Dize). O que usa da API continua: `PluginContext.call_mcp` existe, os hooks continuam em `VALID_HOOKS`, e o único import é `hermes_constants.get_hermes_home` (público, sem alerta do novo `plugin_validate_private_imports`).
5. Teste: `hermes plugins list` mostra o plugin carregado; um turno com MCP gbrain injeta contexto.

## HON-01 — ingest Honcho do Arconte
1. `scripts/dize_honcho_arconte_ingest.py`, teste `tests/scripts/test_dize_honcho_arconte_ingest.py`.
2. Ingestão redigida do state.db para o Honcho; resolve o plugin instalado em `HERMES_HOME/plugins/honcho`.
3. **Quem:** só Arconte. **v0.22.5:** MANTER. Lê `messages(id, role, content, timestamp, compacted, active, display_kind)` e `sessions(id, source, chat_type, session_key, chat_id, thread_id, origin_json, started_at)` em modo somente leitura; todas as colunas continuam no SCHEMA_SQL da v0.22.5.

## TST-01 — isolamento de testes
1. `scripts/run_tests_parallel.py` (`env.pop("HERMES_HOME")`), `tests/conftest.py::_feishu_state_home_guard`, `tests/scripts/test_run_tests_parallel.py::test_runner_isolates_and_removes_hermes_home`.
2. Um shell de gateway exporta a home viva; sem isso, os testes escreveriam na home real.
3. Frota/dev. **v0.22.5:** MANTER (runner sem mudança upstream; o conftest mudou upstream em outros pontos e o hunk aplicou).
4. Relevante para qualquer bateria rodada no Hefesto; usar também TMPDIR próprio (memória "testes enchem disco").

## TST-02 — testes de hook assíncrono `pre_gateway_dispatch` (conflito)
1. `tests/gateway/test_pre_gateway_dispatch.py`: `import asyncio`, `test_async_hook_skip_is_awaited_before_dispatch`, `test_async_hook_failure_does_not_discard_other_results`.
2. Origem: dceb5e8817 (reconcile v0.19.1), quando a Dize fez o gateway aguardar resultados awaitable do `pre_gateway_dispatch` e isolar falhas. Hoje é **nativo**: `hermes_cli/plugins_dispatch.py::PluginManager.ainvoke_hook` ("async def callbacks are awaited... per-callback isolation"), chamado por `gateway/run_inbound_hooks.py::_hm_pre_gateway_dispatch_hook` via `hermes_cli.lifecycle.ainvoke_hook`. O código Dize não está mais no delta; só os testes.
3. **Quem:** plugins com hook assíncrono `pre_gateway_dispatch` (o teste usa WhatsApp; consumidores reais INCERTOS: roteador do Kael, inbox do Hefesto).
4. **Conflito:** os dois lados acrescentaram testes no fim do arquivo (upstream +627 linhas, Dize +48). **Resolução:** versão da v0.22.5 + `import asyncio` + os dois testes Dize anexados com seu próprio `@pytest.mark.asyncio`. Candidata: `/tmp/claude-0/-root/c660d3d1-070f-43b9-92dc-d576cdca074a/scratchpad/resolved/tests/gateway/test_pre_gateway_dispatch.py` (830 linhas, 15 testes, todos passando na wt-misc). As funções `_fake_hook` dentro dos testes Dize são código morto, inofensivo.

---

## Respostas específicas

### a) `multiplex_profiles: false` e migração de config
- **Upstream v0.22.5 pura: NÃO respeita.** `resolve_multiplex_mode` trata `false` como aposentado. Sem bloqueio, decide `retired-opt-out` → multiplex ligado, `persist_resolved_default` grava `gateway.multiplex_profiles: true` no config.yaml do perfil default e deixa o marcador `.multiplex_opt_out_rewritten`.
- **Com MX-01 (aplicou limpo): SIM.** Decisão `(False, "config")`, nenhuma escrita (teste `test_explicit_single_profile_never_rewrites_or_discovers_other_profiles` passa na v0.22.5). Os caminhos novos (`_multiplex_for_added_profile`, `auto_multiplex_migration`) não armam o multiplex nesse caso.
- Outra via de reescrita: `maybe_auto_migrate_after_update` no `hermes update` faz `_write_multiplex_flag(True)` se houver 2 ou mais perfis "dobráveis" com gateway próprio e `auto_multiplex_migration` não estiver `false`. Perfis com `gateway.standalone: true` ficam fora (`build_migration_plan` → `standalone_by_config`), então o Hefesto não entra. Num host multi-perfil sem standalone, isso ignoraria o `false`. Recomendo `gateway.auto_multiplex_migration: false` no default de cada host.
- **Migração de config preserva chaves desconhecidas: SIM.**
  - `_config_version` é 51 no fork, na v0.22.1 e na v0.22.5, então não há passo de versão. Só rodam os `STAMP_INDEPENDENT_STEPS` {46 (MCP `disabled`→`enabled:false`), 50 (chaves tirith)}, e apenas se `get_missing_config_fields()` acionar a migração.
  - Defaults novos não são gravados ("New default keys are NOT materialised to disk").
  - `save_config` → `_strip_default_values` remove só valores iguais ao default **que não estavam explícitos no YAML**; chaves sem default (ex.: `agent.prime_*`, `compression.summary_policy`) nunca são removidas. `_canonicalize_config` mexe só em `max_turns`/`model`.
  - `skills.selection_policy` e `compression.idle_compact_floor_tokens` são defaults **Dize** em `config_defaults.py`; se esse patch se perder, viram chaves desconhecidas mas continuam preservadas.
  - Ressalva: qualquer escrita via `save_config` reformata o YAML (comentários podem se perder). Fazer backup do config.yaml antes.

### b) Migração do banco 31 → 32
- **O que faz:** `SCHEMA_VERSION = 32` em `hermes_state_common.py`, com a nota "v32 (#140315): no data migration". Em `hermes_state_schema.py::_run_data_migrations`: "v32: nothing to run here". O formato declarado sobe de forma aditiva em toda abertura com escrita, via `executescript(SCHEMA_SQL)` + `_reconcile_columns()`. Diferenças de SCHEMA_SQL entre v0.22.1/fork e v0.22.5 (o fork não muda o schema):
  - `sessions`: coluna nova `transcript_revision INTEGER NOT NULL DEFAULT 0` (ALTER ADD COLUMN, O(1)); índice parcial novo `idx_sessions_local_created_started ON sessions(created_source, started_at DESC) WHERE source='local'`.
  - `messages`: nenhuma coluna nova. Trigger novo `messages_transcript_revision_update` (AFTER UPDATE OF role, content, api_content, tool_calls, ... → `UPDATE sessions SET transcript_revision = transcript_revision + 1`). **Plugin que faz UPDATE em `messages` passa a escrever também em `sessions`** (mesma transação; inofensivo, mas segura o lock um pouco mais).
  - `session_admissions`: nenhuma coluna nova; índice parcial `idx_session_admissions_cron_request ... WHERE principal_id = CRON_PRINCIPAL_ID`.
  - `delivery_obligations`: **sem mudança.** Não está no SCHEMA_SQL; é criada por `gateway/delivery_ledger.py::_initialize_schema`, idêntico entre v0.22.1 e v0.22.5.
  - Tabelas próprias da Dize (ex.: `hefesto_prime_inbox`): a migração só roda DDL `IF NOT EXISTS` das tabelas declaradas e nunca toca tabelas desconhecidas. Os `DROP TABLE` da v0.22.5 em `hermes_state*` são só de FTS, probes e `*_new` próprios.
  - Também: `_stamp_settled_fts_layout` (o carimbo de FTS passou para depois de `_init_fts`) e o backfill de `message_uid` agora por `rowid`. `FTS_SQL`/`messages_fts_src` são idênticos, então não se espera rebuild de FTS na primeira abertura (INCERTO-baixo).
- **Rollback:** a v0.22.1 abrindo um banco v32 não recusa (só atualiza a versão se `current < SCHEMA_VERSION`). A coluna e o trigger extras ficam. O trigger que bloqueia apagar sessões com histórico de runtime já existia.
- Fora do escopo, a conferir por quem cuida de `hermes_state_messages.py`: a v0.22.5 tem `hermes_state_prune_rewrite.py` (reescrita in-place de linhas pela poda proativa, #124102). Plugin que lê `messages.content` direto pode ver conteúdo podado. INCERTO.

### c) `hermes update` da v0.22.5: skills, `sync_named_profiles`, `--no-gateway-restart`, `--plan`
- **Skills modificadas pelo usuário** (`tools/skills_sync.py`, lógica igual à da v0.22.1, só renomes `_dir_hash`→`dir_hash`, `_read_skill_name`→`read_skill_name`):
  - skill com hash diferente da origem: **pulada** ("~N user-modified"), nunca sobrescrita;
  - skill sem modificação cuja versão empacotada mudou: **atualizada** (↑);
  - empacotada nova: **copiada** (+);
  - apagada pelo usuário: não volta.

  O `_sync_profiles_after_update` roda `seed_profile_skills` para **todos** os perfis (subprocess com `HERMES_HOME` do perfil). **Além disso, o gateway sincroniza skills no próprio boot** (`gateway/run_bootstrap.py::_sync_skills`, igual na v0.22.1) e o `hermes chat` faz o mesmo em segundo plano. Logo, as skills do Hefesto serão sincronizadas no primeiro restart de qualquer forma. Skills "fixadas" do Hefesto que forem cópia idêntica de uma empacotada **serão atualizadas** pela upstream.
- **`updates.sync_named_profiles`** é só Dize (UPD-01). Com `false`, o update não sincroniza nenhum perfil, nem `.env`, nem Honcho. Não evita a sincronização no boot do gateway. Para congelar skills de verdade: snapshot e diff de `<home>/skills` antes e depois, ou marcador `--no-skills` no perfil (só essenciais).
- **`--no-gateway-restart`** existe (já existia na v0.22.1): atualiza código e dependências e adia o restart da frota. Na v0.22.5 isso também **desarma a pausa POSIX** (`update_cmd_posix_pause.arm_pause` retorna None), que normalmente drena e para os gateways antes do primeiro checkout e os religa depois da sincronização de dependências. Sem a flag, o gateway do Hefesto é drenado (espera `agent.restart_after_turn_timeout`, que enfileira tópicos; ver memória "reload enfileira tópicos") fica parado durante a troca de código e a sincronização de dependências, e é religado antes do build (o restart da frota depois pula o que já foi religado, `_RESTARTED`). Ou seja: uma parada drenada mais um start, com o Hefesto fora do ar durante a sincronização de dependências. O registro fica "gateway_restart skipped: --no-gateway-restart: deferred, marker kept".
- **`--plan`**: só leitura (`hermes_cli/update_inventory.py`). Mostra o tipo de instalação (git/docker/nix), cada runtime Hermes em execução (gateway/dashboard/serve) de todos os perfis com supervisor, PID, sha/versão do código em execução, e como cada um será reiniciado. Seguro com a frota no ar.
- Fora do escopo, mas importante: o `hermes update` oficial atualiza o checkout contra o branch/canal upstream. Para o fork Dize, o pouso deve ser manual (curado) e só depois as etapas de manutenção.

### d) `runner.adapters[Platform.LOCAL]` ocupado por plugin na v0.22.5
- **Continua conflitando, igual à v0.22.1, agora com mais consumidores.**
  - `gateway/session_local_recovery.py::restore_local_session` (e a criação) exige `isinstance(adapter, LocalSessionAdapter) and adapter.authority is authority`, senão `RuntimeStoreError('runtime_draining')`.
  - `gateway/session_local.py::authorize_local_source` devolve False para qualquer origem LOCAL quando o slot não é `LocalSessionAdapter`.
- **Crons:** cron com agente (`not job.no_agent`) roda via `cron/scheduler_authority.run_canonical_job` → `gateway/session_cron.operation('submit'/'recover')` → sessão LOCAL (`SessionEntry(... platform=Platform.LOCAL)`; `local_adapter_map(authority)[Platform.LOCAL].policies[sid]`). **Com o slot ocupado, cron com agente falha** (`runtime_draining`, ou AttributeError em `recover`). Já era assim na v0.22.1; a memória diz que os crons do Hefesto funcionam, o que é coerente com serem todos `no_agent` (scripts). **Verificar antes do update** que não há job de cron com agente no perfil hefesto.
- **Sessões locais / CLI:** na v0.22.5 a CLI clássica e o `-q` não carregam plugins e rodam o turno no gateway (PLG-7, `GatewaySessionStore`). `hermes chat`/`-q`/`-z` continuam quebrados no Hefesto; nada melhora nem piora.
- **Novos consumidores da v0.22.5 (mesma falha fechada):** `gateway/plugin_keyless_inject.py` (`ctx.inject_message` sem chave a partir de sessão LOCAL), `gateway/run_plugin_injection.py` (injeção de plugin em rota LOCAL), `gateway/session_control_actions.py`, `gateway/slash_commands_goals.py`, `gateway/session_commands.py`. `gateway/run_runtime.py::_drop_retired_local_adapter` só apaga o slot se o adapter tiver `.authority` aposentada; como só roda para perfil secundário servido, não age no Hefesto (perfil único).
- **Backends de delegação (`agent/delegation_backends.py`, `agent/delegation_registry.py`, novos):** não referenciam `Platform.LOCAL` nem `runner.adapters`, então não há conflito de slot. Se o plugin `dize-project-delegation` continua interceptando `delegate_task` com o novo roteamento por `delegation.backend` (padrão `default`), é INCERTO e fica com quem inventaria delegação.
- Correção estrutural (fora do escopo, a decidir): o plugin não deve ocupar o slot LOCAL (usar `Platform` próprio ou subclassar `LocalSessionAdapter`).
