# Alterações Dize carregadas sobre o Hermes upstream

Base: upstream `v0.22.5` (bf0203a794a). Origem do delta: `dizevolv-maintained/hermes-hefesto` @ 14dc95fb (= Arconte exceto `gateway/session_envelope.py`).
Detalhe de cada item (motivo, evidência, teste comportamental): `docs/dize/inventario-v0.22.5/`.

## Regra para a próxima atualização

1. Aplicar `git diff <base-upstream-atual> <este branch>` sobre o novo upstream com `git apply --3way` num clone isolado.
2. Para cada ID abaixo: conferir se continua necessário (o upstream pode ter absorvido), resolver conflito, manter o teste.
3. Remover só com prova no código do upstream (citar arquivo/função). Hotfix vivo que não estiver aqui não existe: entra aqui antes de qualquer atualização.
4. Rodar a suíte dos plugins do Hefesto (repo DizeHefesto) contra o candidato e comparar com a referência do motor atual.

## Situação no candidato v0.22.5

| ID | Alteração | Quem precisa | Situação |
|---|---|---|---|
| GW-01 | Admissão de automação apesar do reply id do humano | frota (crítico no Hefesto) | **REMOVER** (upstream já tem) |
| GW-02 | IDs de delegação assíncrona atravessam a admissão de automação | código na frota; consumidor = plugins Hefesto | **ADAPTAR** (juntar com `heartbeat_tick`/`_tick_metadata`) |
| GW-03 | `gateway_event_context` para observadores (plugins) | só Hefesto (plugins) | MANTER |
| GW-04 | Conclusão durável sem rota é descartada (sentinela NO_ROUTE / origem CLI) | frota | **ADAPTAR** (só docstring; semântica ok) |
| GW-05 | Recibo do watcher com retry e status de entrega | frota | MANTER |
| GW-06 | `/resume` de transcrição de delegado vira dona da rota | frota | **ADAPTAR** (juntar com `_closed_local_target`) |
| GW-07 | Espera de reinício/drenagem conta delegações nativas do Hefesto | só Hefesto (caminho fixo) | MANTER (+ correção recomendada) |
| GW-08 | Descartar turno perdido em parada abrupta (opt-in) | Hefesto (env ligada) | **ADAPTAR** (upstream extraiu `recover_profile_native_sessions`) |
| GW-09 | Retomar turno cortado por reinício planejado (opt-in) | Hefesto (env `continue`) | **ADAPTAR** (mesmo ponto) |
| GW-10 | Backport v0.22.2 do host-record após reboot | frota | **REMOVER** (v0.22.5 é superconjunto) |
| GW-11 | Metadados de mídia Telegram do Hefesto na admissão nativa | **só Hefesto** | **ADAPTAR** (trivial) |
| GW-12 | Gateway sem multiplex serve/agenda só o perfil de lançamento | frota | MANTER |
| GW-13 | Hook de plugin `gateway_housekeeping_tick` | frota (plugins) | MANTER |
| GW-14 | `_compressed_summary` preservado no replay | frota | **REMOVER** (upstream `scaffolding_replay_fields`) |
| GW-15 | `dm_allow_from` no ponte config.yaml → extra | frota | MANTER (depende dos adapters) |
| GW-16 | `send_message` Telegram avulso: transporte de fallback, thread/chat no resultado, fecha cliente | frota | MANTER |
| GW-17 | Texto dos avisos de delegação (ponteiros de arquivo, hora do snapshot, recusa de política) | frota (origem Hefesto) | MANTER |
| GW-18 | Dreno sem filtro nunca consome async_delegation; dono no CLI por linhagem | frota (CLI) | MANTER |
| GW-19 | `_reset_for_tests` espera workers | testes | MANTER |
| AG-01 | Pool mantém o prazo 429 conhecido quando o retry vem sem reset | frota (crítico p/ Hefesto) | MANTER |
| AG-02 | `token_is_blocked` trata credencial esgotada ou morta como bloqueada | frota (Hefesto) | MANTER |
| AG-03 | Merge de disco do auth não encurta o prazo 429 | frota (Hefesto) | MANTER |
| AG-04 | Cliente aux Anthropic escolhe por modelo e recusa token bloqueado, inclusive em cache | frota (Hefesto) | MANTER (conflito resolvido) |
| AG-05 | Aux Anthropic em cooldown cai para a cadeia principal de fallback | frota (Hefesto) | MANTER (conflito resolvido) |
| AG-06 | Rotação aux usa erro estruturado; sem retry na mesma chave se o reset ainda é futuro | frota | MANTER |
| AG-07 | Idle compaction usa o relógio durável da SessionDB | Hefesto (gateway) | MANTER |
| AG-08 | `compression.idle_compact_floor_tokens` (piso absoluto) + parsing robusto | Hefesto | MANTER |
| AG-09 | Idle compaction respeita o bloqueio automático (anti-thrash/cooldown) | frota | MANTER |
| AG-10 | Idle compaction arma o checkpoint nativo Responses em vez de resumo local | só Codex/Responses | MANTER (opcional; pode sair por desuso) |
| AG-11 | Compressor busca de novo o token OAuth Anthropic antes do resumo | Hefesto/frota OAuth | MANTER (em parte redundante) |
| AG-12 | `compression.summary_policy: authorized_task_v1` (continuidade de tarefa autorizada) | Hefesto | MANTER |
| AG-13 | `agent.pre_verify_on_stop` (o gate de parada roda sem edição de arquivo) | **Hefesto (aceite independente)** | MANTER |
| AG-14 | Turno final entregue (com rodapés e hooks) vira a linha durável do assistente | frota | MANTER |
| AG-15 | `gateway_event_context` repassado ao hook `pre_llm_call` | Hefesto (plugins) | MANTER (depende do delta gateway) |
| AG-16 | `skills.selection_policy` (broad / task_relevant) + docs | Hefesto | MANTER (conflito de doc resolvido) |
| AG-17 | Data URL de visão usa o MIME detectado nos bytes | frota | MANTER |
| AG-18 | Codex app-server: janela do provedor limitada pelo `context_length` configurado | só Codex | MANTER (opcional; pode sair por desuso) |
| AG-19 | Codex app-server session: `turn_timeout=None` + `inactivity_timeout` | Codex/plugin? | INCERTO (manter até checar os plugins) |
| AG-20 | `tool_executor.py`: imports duplicados | ninguém | REMOVER (conflito resolvido = v0.22.5) |
| AG-21 | default `updates.sync_named_profiles: True` | Arconte (host dono da frota) | MANTER (o consumidor está fora deste escopo) |
| MX-01 | `gateway.multiplex_profiles: false` explícito é respeitado e nunca reescrito | frota (crítico p/ Arconte; Hefesto já protegido também por `gateway.standalone`) | MANTER |
| UPD-01 | `updates.sync_named_profiles: false` desliga a sincronização de perfis no `hermes update` | só Arconte | MANTER |
| PLG-01 | hook `gateway_housekeeping_tick` (VALID_HOOKS + chamada no housekeeping) | plugins de perfil Dize (quem registra: INCERTO) | MANTER |
| CLI-01 | dono das delegações assíncronas na CLI (`declare_stateless_channel` no `-q`, `is_compression_ancestor`) | frota | MANTER (efeito INCERTO) |
| CLI-02 | correção de closure `e` em `_present_store_warning` (Py 3.14) | frota | MANTER |
| ST-01 | rótulo de holder do state.db sem argv (não vaza segredo) | frota | MANTER |
| CRON-01 | lifecycle guard: script grande é logado e pulado, não recusa o job | Hefesto (código da frota) | MANTER |
| TG-01 | pareamento do portal sobrevive ao pré-filtro do Telegram (união com allowlist) | frota (incidente Atena; Hefesto usa `allow_from`/`group_allow_from`) | MANTER |
| TG-02 | teto de DM do Telegram `dm_allow_from` / `TELEGRAM_DM_ALLOWED_USERS` | Hefesto (usa `telegram.dm_allow_from`) + frota | MANTER (depende de `gateway/config_loader.py`) |
| DC-01 | teto de DM do Discord `dm_allow_from` / `DISCORD_DM_ALLOWED_USERS` (+ botões) | frota (Hefesto não usa Discord) | MANTER |
| WA-01 | bridge WhatsApp (Baileys): recibos `/delivery-status` e `/resolve-whatsapp` | só Atlas-Kael (WhatsApp) | MANTER |
| GB-01 | plugin `dize-gbrain-librarian` + `scripts/dize_gbrain_library_projector.py` | Arconte (GBrain); perfis que habilitarem | MANTER (só Dize) |
| HON-01 | `scripts/dize_honcho_arconte_ingest.py` | só Arconte | MANTER (só Dize) |
| TST-01 | isolamento de teste: `run_tests_parallel` tira `HERMES_HOME`; guarda Feishu no conftest | frota/dev | MANTER |
| TST-02 | testes de regressão de hook assíncrono `pre_gateway_dispatch` | frota (plugins com hook async) | código já nativo; **testes MANTER** |
| F11 | Espera de reinício conta só delegações vivas (`_LIVE_STATES`); registro com `timeout`/`unknown` não vira unidade fantasma | Hefesto | NOVO neste candidato (730868bb), testes em `tests/gateway/test_restart_drain.py` |
| F12 | Kanban: tarefa já decomposta e devolvida para triagem não paga nova chamada ao decompositor | frota | NOVO neste candidato (e35adc20), testes em `tests/hermes_cli/test_kanban_decompose.py` e `tests/gateway/test_kanban_auto_decompose_secret_scope.py` |

## Fora deste repositório (plugins do perfil Hefesto, repo DizeHefesto)

| Item | Situação |
|---|---|
| `dize-project-delegation/local_adapter.py` herda `LocalSessionAdapter` (slot LOCAL) | instalado 26/10; suíte do plugin sem falha nova no candidato |
| `dize-project-delegation/tests/test_local_transport.py`: dublê de `SessionEntry` precisa de `platform` (v0.22.5 lê `entry.platform` em `_closed_local_target`) | ajustar no repo antes de promover; só teste |
| `kanban.auto_decompose: false` no config do Hefesto | ligado 26/10 como trava; com F12 pode voltar a `true` se o Lucas quiser |
| `dize-project-delegation/client.py` repo ≠ perfil (F6) | decisão do Lucas |

## Ressalvas conhecidas do upstream que afetam o Hefesto

- `sessions.auto_prune` passou a `false` por padrão; `vision.replay_recent_images: 3` reenvia imagens recentes em turnos reconstruídos.
- A nota de LSP da v0.22.5 é uma linha `user` sem marca de sintética: `_is_real_user_message` a conta como humana.
- Banco 31→32: só marca versão; trigger novo em `messages` atualiza `sessions.transcript_revision` em todo UPDATE (inclusive de plugin).
- Testes do upstream com poluição de ordem: `tests/e2e/core/upgrade/test_config_roundtrip_properties.py` antes de `tests/gateway/test_discord_component_auth.py` derruba 14 testes (igual na v0.22.5 pura).
