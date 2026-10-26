# Inventário Dize — gateway / notificações / delegação (Hermes v0.22.1 → v0.22.5, Hefesto)

Escopo: `gateway/*` do delta, `tools/process_registry*.py`, `tools/async_delegation.py`,
`tools/delegate_tool_progress.py`, `tools/send_message_senders.py`,
`hermes_cli/cli_process_notifications.py` e os testes que os cobrem.
Base: `git diff v0.22.1^{commit} fork/hermes-hefesto`. Alvo: `v0.22.5^{commit}` (bf0203a794).
Trabalho só leitura; resoluções candidatas em `scratchpad/resolved/`.

Verificação feita: montei uma worktree própria (`scratchpad/wt-gateway`, base v0.22.5 + todo o delta
aplicado do probe + as resoluções de conflito) e rodei os testes Dize/afetados (venv do Hermes,
Python 3.14, `TMPDIR` próprio). Resultado na seção "Testes executados".

## Tabela-resumo

| id | mudança | arquivos | quem precisa | v0.22.5 | conflito? | risco p/ tópicos |
|---|---|---|---|---|---|---|
| GW-01 | Admissão de automação apesar do reply id do humano | session_automation | frota (crítico no Hefesto) | **REMOVER** (upstream já tem) | sim (resolvido = upstream) | baixo após update (alto se regredir) |
| GW-02 | IDs de delegação assíncrona atravessam a admissão de automação | session_automation, session_envelope, run_notifications | código na frota; consumidor = plugins Hefesto | **ADAPTAR** (juntar com `heartbeat_tick`/`_tick_metadata`) | sim | **alto** |
| GW-03 | `gateway_event_context` para observadores (plugins) | run_turn, run_turn_runner, turn_context (+ agent/turn_context) | só Hefesto (plugins) | MANTER | não | médio |
| GW-04 | Conclusão durável sem rota é descartada (sentinela NO_ROUTE / origem CLI) | run_notifications | frota | **ADAPTAR** (só docstring; semântica ok) | sim | médio |
| GW-05 | Recibo do watcher com retry e status de entrega | run_notifications | frota | MANTER | não | baixo |
| GW-06 | `/resume` de transcrição de delegado vira dona da rota | slash_commands_session, run_notifications_ownership | frota | **ADAPTAR** (juntar com `_closed_local_target`) | sim | médio |
| GW-07 | Espera de reinício/drenagem conta delegações nativas do Hefesto | run_shutdown | só Hefesto (caminho fixo) | MANTER (+ correção recomendada) | não (aplicou limpo) | **alto** |
| GW-08 | Descartar turno perdido em parada abrupta (opt-in) | dize_lost_turn_recovery, run_runtime | Hefesto (env ligada) | **ADAPTAR** (upstream extraiu `recover_profile_native_sessions`) | sim | médio |
| GW-09 | Retomar turno cortado por reinício planejado (opt-in) | dize_lost_turn_recovery, run_runtime | Hefesto (env `continue`) | **ADAPTAR** (mesmo ponto) | sim | **alto** |
| GW-10 | Backport v0.22.2 do host-record após reboot | host_rendezvous | frota | **REMOVER** (v0.22.5 é superconjunto) | não (arquivo = v0.22.5) | baixo |
| GW-11 | Metadados de mídia Telegram do Hefesto na admissão nativa | session_envelope | **só Hefesto** | **ADAPTAR** (trivial) | sim | **alto** |
| GW-12 | Gateway sem multiplex serve/agenda só o perfil de lançamento | run.py | frota | MANTER | não | médio |
| GW-13 | Hook de plugin `gateway_housekeeping_tick` | run.py (+ hermes_cli/plugins) | frota (plugins) | MANTER | não | baixo/INCERTO |
| GW-14 | `_compressed_summary` preservado no replay | run.py | frota | **REMOVER** (upstream `scaffolding_replay_fields`) | não (aplicou duplicado) | baixo |
| GW-15 | `dm_allow_from` no ponte config.yaml → extra | config_loader | frota | MANTER (depende dos adapters) | não | baixo |
| GW-16 | `send_message` Telegram avulso: transporte de fallback, thread/chat no resultado, fecha cliente | send_message_senders | frota | MANTER | não | baixo |
| GW-17 | Texto dos avisos de delegação (ponteiros de arquivo, hora do snapshot, recusa de política) | process_registry_notifications, delegate_tool_progress | frota (origem Hefesto) | MANTER | não | baixo |
| GW-18 | Dreno sem filtro nunca consome async_delegation; dono no CLI por linhagem | process_registry, cli_process_notifications | frota (CLI) | MANTER | não | baixo |
| GW-19 | `_reset_for_tests` espera workers | async_delegation | testes | MANTER | não | nenhum |

Contagem: MANTER 9 (GW-03, 05, 07, 12, 13, 15, 16, 17, 18) + 1 só de teste (GW-19); ADAPTAR 6
(GW-02, 04, 06, 08, 09, 11); REMOVER 3 (GW-01, 10, 14); INCERTO 0 como situação (há incertezas
pontuais anotadas em GW-03, GW-06, GW-13).

Conflitos do meu escopo e resolução (arquivos em `scratchpad/resolved/`):

| arquivo | resolução |
|---|---|
| gateway/run_notifications.py | docstring = texto v0.22.5 + frase Dize do sentinela; sentinela/`_raw_route_is_terminal` movidos para antes do comentário de `_DURABLE_CLAIM_OPS` (cosmético) |
| gateway/run_notifications_ownership.py | manter os dois métodos: `_closed_local_target` (upstream) e `_gateway_resume_promotion` (Dize) |
| gateway/run_runtime.py | manter `recover_profile_native_sessions` da v0.22.5; ganchos Dize só no caminho de boot (`recover_gateway_native_sessions`) via novo `_dize_startup_recovery` |
| gateway/session_automation.py | `_route_identity`/`pop('message_id')` = upstream (GW-01 some); `_AUTOMATION_METADATA` = upstream + 2 chaves Dize; `_tick_metadata` **e** `dize_delegation_metadata` nos dois snapshots |
| gateway/session_envelope.py | manter `_MEDIA_FIELDS` (upstream) **e** `_DIZE_TELEGRAM_METADATA*` |
| tests/gateway/test_pre_gateway_dispatch.py | já resolvido por outro agente em `resolved/tests/...` (v0.22.5 + `import asyncio` + 2 testes Dize no fim). Concordo; passa. Cobre hooks async de plugin (`hermes_cli/plugins.py`), fora deste escopo |

---

## GW-01 — Admissão de automação apesar do reply id do humano

- Arquivos/funções: `gateway/session_automation.py` — `_route_identity`, `snapshot_automation`
  (comparação de fonte e `source.pop('message_id')`).
- Testes Dize: `tests/gateway/test_producer_owner_continuity.py` (+51 linhas, 6d2216c392).
- Motivo (commit 6d2216c392): desde 0.22, toda conclusão de processo, watch match e resultado de
  delegação assíncrona num tópico Telegram era recusada `admission_conflict` (a fonte emprestada do
  humano tem `message_id`, o evento sintético não — #52694). O produtor reenfileirava a cada 1–2 s
  para sempre; **nenhum turno de automação era admitido no Hefesto**.
- Quem precisa: frota (qualquer gateway com tópicos/reply id); crítico no Hefesto.
- v0.22.5: **REMOVER**. Prova (v0.22.5 `gateway/session_automation.py`):
  `def _route_identity(source): return {key: value for key, value in source.to_dict().items() if key != 'message_id'}`,
  usado em `snapshot_automation` (`if (_route_identity(restored.source) != _route_identity(event.source) ...`)
  e `source.pop('message_id', None)` com o comentário "Never anchor the wake to the stale human message".
  Mesma semântica da nossa versão. `admit_route_wake` da v0.22.5 também usa `restored.source`.
- Conflito: sim (blocos 2, 4, 5 de session_automation) → fica a versão upstream.
- Teste comportamental: num tópico, iniciar `terminal(background=true, notify_on_complete=true)`
  (ou um `delegate_task` async); ao terminar, o tópico recebe UM turno de automação; no gateway.log
  não aparece `admission_conflict` nem reenfileiramento em loop do mesmo evento.
- Risco: baixo após update (upstream cobre; os testes Dize de producer_owner_continuity passam
  com o código upstream). Seria alto se a resolução do conflito pegasse a versão antiga sem o pop.

## GW-02 — IDs de delegação assíncrona atravessam a admissão de automação

- Arquivos/funções:
  - `gateway/run_notifications.py`: `_watch_notification_metadata` (grava
    `async_delegation_ids` e `async_delegation_interim` quando `type == "async_delegation"`);
    `_deliver_completion_event…` (bloco que monta `_admitted_delegation_ids` com o id principal +
    irmãos realmente reivindicados).
  - `gateway/session_automation.py`: `_AUTOMATION_METADATA` (+2 chaves), `dize_delegation_metadata`,
    `snapshot_automation`, `snapshot_local_automation`, `restore_local_automation`.
  - `gateway/session_envelope.py`: `restore_native` (restaura as chaves no replay).
- Testes Dize: `tests/gateway/test_dize_automation_delegation_metadata.py`.
- Motivo: landing 0.22.1 (a32910dc08: "async delegation metadata allowed through v0.22 automation
  admission"); a parte de `run_notifications` veio dos hotfixes do Hefesto (3336def46a). Os plugins
  do Hefesto leem esses IDs pelo `gateway_event_context` (GW-03) para casar o resultado com o filho.
- **Acoplamento crítico**: `snapshot_automation` recusa `invalid_params` qualquer chave fora de
  `_AUTOMATION_METADATA`. Se a parte de `run_notifications` for mantida e a de `session_automation`
  não, **todo resultado de delegação assíncrona é recusado** (e com a v0.22.5 vira recusa permanente
  `invalid_params` ∈ `PERMANENT_REFUSAL_REASONS` → descartado após 8 tentativas / `release` no ledger).
  As duas partes têm de ir juntas.
- Quem precisa: código em toda a frota; consumidor real só plugins do Hefesto.
- v0.22.5: **ADAPTAR**. Upstream acrescentou `heartbeat_tick` em `_AUTOMATION_METADATA` e
  `_tick_metadata(...)` nos snapshots. Resolução: união das chaves e das duas funções
  (`notification = ... | _tick_metadata(...) | dize_delegation_metadata(...)`;
  `descriptor.update(_tick_metadata(...))` + `descriptor.update(dize_delegation_metadata(...))`).
  `admit_process_notice` (novo na v0.22.5) só acrescenta `heartbeat_tick` e repassa `event.metadata`
  intacto, então as chaves Dize chegam ao snapshot.
- Conflito: sim (blocos 1, 3, 6 de session_automation; restore em session_envelope aplicou limpo).
- Teste comportamental: disparar `delegate_task` assíncrono com 2 filhos num tópico; no turno de
  resultado, o plugin deve receber `gateway_event_context.metadata.async_delegation_ids` com os dois
  ids; reiniciar o gateway com o resultado ainda na fila → o replay mantém os ids
  (`restore_native`). No log: nenhum `Native admission refused`/`invalid_params` para automação.
- Risco: **alto** (erro de merge = Hefesto não recebe resultado de filhos).

## GW-03 — `gateway_event_context` para observadores (plugins)

- Arquivos/funções: `gateway/run_turn.py` (`_event_observer_context`, passa
  `gateway_event_context` nas duas chamadas de `_run_agent` — turno e follow-up da fila — e em
  `_run_agent_inner`), `gateway/turn_context.py` (campo `gateway_event_context`),
  `gateway/run_turn_runner.py` (`agent._gateway_event_context = dict(...)` a cada turno, inclusive
  vazio). Fora do escopo mas acoplado: `agent/turn_context.py` passa
  `gateway_event_context=` ao hook `pre_llm_call`.
- Testes Dize: nenhum direto (só o docstring de test_dize_automation_delegation_metadata cita).
- Motivo: hotfix do Hefesto (3336def46a; o marcador não existe antes dele). Identidade confiável do
  evento interno (ids de delegação, `gateway_session_id`, fonte) para plugins, fora do prompt, e
  "rebind" vazio para que um follow-up humano nunca herde ids antigos.
- Quem precisa: só Hefesto (plugins do perfil).
- v0.22.5: MANTER. Aplicou limpo; conferi que `_run_agent(**turn_kwargs)` repassa a
  `_run_agent_inner`, e que o ponto de bind em `TurnRunner` ainda existe
  (`agent._notification_config = ctx.user_config` logo abaixo).
- INCERTO: a lista inclui a chave `dize_delegation`, que nenhum código do repo grava (e que
  `_AUTOMATION_METADATA` recusaria num evento de automação). Provável resto do caminho clássico/
  pré-0.22; inofensivo. Não consegui ver o plugin (fora do repo) para confirmar quais chaves ele lê.
- Teste comportamental: plugin de teste (ou log temporário no hook `pre_llm_call`) — no turno de
  resultado de delegação, `gateway_event_context["internal"] is True` e `metadata` traz os ids;
  na mensagem humana seguinte do mesmo tópico, `gateway_event_context == {}`.
- Risco: médio (sem ele o plugin perde a correlação filho↔resultado; o turno em si roda).
- Sugestão: acrescentar um teste unitário de `_event_observer_context` + rebind vazio.

## GW-04 — Conclusão durável sem rota é descartada (sentinela NO_ROUTE / origem CLI)

- Arquivos/funções: `gateway/run_notifications.py` — `_SYNTHETIC_EVENT_NO_ROUTE`,
  `_raw_route_is_terminal`, `_inject_watch_notification(no_route_sentinel=...)`, o pré-voo de
  rota raw (retorna `True` = disposição terminal para origem só CLI/desktop),
  `_deliver_completion…` (`unroutable` → operação `"drop"` no ledger durável).
- Testes Dize: `tests/gateway/test_completion_delivery.py`
  (`test_unroutable_async_event_remains_retryable` ajustado,
  `test_unroutable_durable_async_event_is_terminally_acknowledged`).
- Motivo: o sentinela existe desde a linha 0.21 (presente antes dos hotfixes do Hefesto); o
  `_raw_route_is_terminal` entrou no landing 0.22.1. Sem isso, uma conclusão durável sem rota
  (ex.: delegação disparada por CLI num host sem api_server) era "release" e voltava a cada boot,
  indefinidamente.
- Quem precisa: frota.
- v0.22.5: **ADAPTAR** (só texto). A v0.22.5 mudou a recusa: `WakeNotAccepted.permanent`,
  `_watch_pin_gone`, backoff (`_requeue_refused_watch_event`, `_back_off_refused_completions`) e,
  no caminho durável, `refused = not exc.permanent`. Os nossos retornos de sentinela acontecem
  **antes** do `try` (ramos "sem source"), então não interagem com a nova lógica de recusa; o novo
  `return None` de `pin_gone` só vale para `raise_not_accepted=False` (caminho não durável). A
  precedência `"drop" if unroutable else "complete" if accepted else "defer" if refused ... else
  "release"` continua correta.
- Conflito: sim (docstring) → texto upstream + frase Dize.
- Teste comportamental: num host sem api_server, criar linha pendente em `async_delegations` com
  `session_key` raw (origem CLI) e reiniciar: o gateway marca a linha `dropped` uma vez e não a
  reoferece a cada boot (antes: reaparecia sempre).
- Risco: médio (sem ele: ruído e retrabalho a cada boot, não perda de resultado em tópico).

## GW-05 — Recibo do watcher com retry e status de entrega

- Arquivos/funções: `gateway/run_notifications.py::_send_watcher_message` — até 3 tentativas para
  `SendResult.retryable`, respeita `retry_after`, grava `watcher["delivery"]`
  (`DELIVERED`/`FAILED`, `message_id`), loga falha com segredo redigido.
- Testes Dize: `tests/gateway/test_background_process_notifications.py` (+31).
- Motivo: linha 0.21 (marcador presente antes dos hotfixes). Antes, erro de envio era engolido.
- Quem precisa: frota.
- v0.22.5: MANTER. Aplicou limpo; `SendResult.retryable`/`retry_after` existem na v0.22.5. A
  v0.22.5 passou a chamar `_send_watcher_message` também no fallback após 8 recusas de admissão —
  beneficia do retry. Ninguém no repo lê `watcher["delivery"]` além do teste (plugins: INCERTO).
- Teste comportamental: processo com `notify_on_complete` e `display.background_process_notifications=result`
  com o adapter respondendo 429 → recibo chega após o `retry_after`; log sem "Watcher delivery error".
- Risco: baixo.

## GW-06 — `/resume` de transcrição de delegado vira dona da rota

- Arquivos/funções: `gateway/slash_commands_session.py::_handle_resume_command` (grava
  `model_config._gateway_resume = {session_id, delegate_from}` quando o alvo tem `_delegate_from`;
  reordena para gravar antes de responder, inclusive em `already_on`);
  `gateway/run_notifications_ownership.py::_gateway_resume_promotion` e o uso em
  `_lookup_completion_owner` (aceita a promoção só se a linhagem de compressão **e** a âncora
  persistida confirmarem).
- Testes Dize: **nenhum** encontrado (procurei `_gateway_resume` em tests/).
- Motivo: a marca `_gateway_resume` já existia na linha 0.21 do Arconte; o helper
  `_gateway_resume_promotion` é a forma do landing 0.22.1. Sem ele, depois de `/resume` numa
  transcrição de subagente, conclusões de trabalho em segundo plano dessa sessão subiam o
  `_delegate_from` até o pai e eram tratadas como de outra rota (descartadas/terminal).
- Quem precisa: frota.
- v0.22.5: **ADAPTAR**. Upstream inseriu `_closed_local_target` (só rota LOCAL, #134889 LC-7) no
  mesmo ponto → manter os dois métodos. `_watch_pin_gone` (novo) usa
  `_classify_completion_owner` → `_lookup_completion_owner`, então respeita a promoção.
  `get_compression_lineage` e `patch_session_model_config` existem na v0.22.5. O `/resume` da
  v0.22.5 não mudou na região (o patch aplicou limpo).
- Conflito: sim → ambos (`resolved/gateway/run_notifications_ownership.py`).
- Teste comportamental: num tópico, `/resume <transcrição de delegado>`, iniciar processo em
  segundo plano e deixá-lo terminar → o resultado entra no tópico; `state.db` da sessão mostra
  `model_config._gateway_resume`. Antes da promoção: log "does not own current route / dropping".
- Risco: médio (cenário raro, mas perda silenciosa de resultado). INCERTO quanto à cobertura —
  recomendo um teste unitário de `_gateway_resume_promotion`.

## GW-07 — Espera de reinício/drenagem conta delegações nativas do Hefesto

- Arquivos/funções: `gateway/run_shutdown.py` — `_native_delegation_units`,
  `_native_delegation_work_snapshot` (lê `tools.async_delegation._records` e
  `tools.delegate_tool_registry._active_subagents` com `acquire(blocking=False)`; só quando
  `get_process_hermes_home() == /root/.hermes/profiles/hefesto`), soma em `_active_work_count`,
  5º elemento em `_drain_work_counts` (drena com o prazo de chat), soma em
  `_awaitable_work_count` (14dc95fb49) e em `_describe_active_work`.
- Testes Dize: `tests/gateway/test_restart_drain.py`
  (`test_native_delegation_units_are_awaitable_restart_work`,
  `test_restart_after_turn_wait_holds_for_native_delegation`) — usam um stub do snapshot; a leitura
  real dos registros **não** tem teste.
- Motivo: hotfixes do Hefesto (3336def46a) + 14dc95fb49. Delegações morrem com o processo; sem
  isso, com só uma delegação em voo, o reinício pulava a espera (`restart_after_turn_timeout`,
  1800 s) e dava só `restart_drain_timeout` (30 s no Hefesto) antes de interromper o filho.
- Quem precisa: só Hefesto (portão pelo caminho).
- v0.22.5: MANTER. **Verificação semântica obrigatória**:
  - `_awaitable_work_count` **não foi reescrito entre v0.22.1 e v0.22.5**: `git diff v0.22.1 v0.22.5
    -- gateway/run_shutdown.py` não toca a função (o corpo é idêntico). A "reconstrução" citada é a
    do merge 0.22.1 (mensagem do 14dc95fb49). Com o patch, a v0.22.5 fica:
    `max(0, non_cron - wedged) + cron_awaitable + len(_native_delegation_units(self))` → a espera
    segura delegações. Correto.
  - `_active_work_count` e `_drain_work_counts` também inalterados upstream; o patch aplicou; não há
    outro consumidor de `_drain_work_counts` fora do arquivo (só um teste que liga o método num stub
    sem o mixin → o `getattr` devolve `[]`).
  - Novidades upstream no desligamento não conflitam: `interrupt_side_agents` (agentes laterais da
    autoridade, threads, não registros de delegação — sem dupla contagem com o overlay),
    `open_restart_wait` (marca `drain_reason="restart_wait"`), e `_interrupt_delegations`
    (`async_delegation.interrupt_all`) continua no fim do stop.
  - Símbolos lidos existem na v0.22.5 com o mesmo formato: `_records`/`_records_lock`,
    `_active_subagents`/`_active_subagents_lock`, campo `delegation_id`, `_LIVE_STATES =
    {"running","stalling","finalizing"}`, `get_process_hermes_home()` ainda segue `HERMES_HOME`.
- **Defeito latente encontrado (já existe hoje no Hefesto, não é da v0.22.5)**: o ramo de status
  só considera terminais `completed/error/failed/interrupted/cancelled/canceled/stalled`. Um filho
  assíncrono que expira recebe status `"timeout"` (`tools/delegate_tool_child_run.py`:
  `status = "timeout" if is_timeout else "error"`; `classify = lambda r: r.get("status") or
  "completed"`). Esse registro concluído fica em `_records` (até 50 concluídos retidos) e o snapshot
  passa a devolver **uma unidade `delegation_unknown` fantasma** — reproduzi:
  `{'status': 'timeout'}` → `[{'kind': 'delegation_unknown', ...}]`. Efeito: todo reinício seguinte
  espera o teto (`restart_after_turn_timeout`, 1800 s) e a drenagem até o prazo, sem trabalho real.
  Correção proposta (alinha com `async_delegation.active_count`):

  ```python
  # em _native_delegation_work_snapshot, ramo "else" (registros de async_delegation)
  live = getattr(module, '_LIVE_STATES', frozenset({'running', 'stalling', 'finalizing'}))
  status = record.get('status')
  if not isinstance(status, str) or not status:
      raise TypeError('Invalid native delegation status')
  if status in live:
      units[('delegation', record_id)] = {...}
  # qualquer outro status é registro concluído retido para consulta: não é trabalho
  ```
- Conflito: não (aplicou limpo).
- Teste comportamental: no Hefesto, disparar delegação assíncrona longa e pedir `/restart`: o log
  mostra "Restart requested with N active work unit(s); deferring stop()" e
  `_describe_active_work` lista `kind: delegation`; o filho termina e o resultado chega antes do
  reinício. E o contrário: sem delegação viva (após um filho com timeout), `/restart` não deve
  esperar.
- Risco: **alto** (sem ele, reinício mata filhos em 30 s e o Hefesto não recebe o resultado; com o
  defeito de `timeout`, reinícios ficam presos 30 min).

## GW-08 — Descartar turno perdido em parada abrupta (opt-in)

- Arquivos/funções: `gateway/dize_lost_turn_recovery.py::discard_lost_chat_turns` (+ `NOTICE` em
  português); chamada em `gateway/run_runtime.py::recover_gateway_native_sessions`.
- Testes Dize: `tests/gateway/test_dize_lost_turn_recovery.py`.
- Motivo (c7fee2b42a): desde 0.22, um turno vivo quando o dono morre (VPS desligada) fica
  `unknown` e pausa o tópico até `hermes sessions discard`. Com
  `HERMES_DIZE_AUTO_DISCARD_LOST_TURNS=true` (ligado no Hefesto), o boot resolve os turnos perdidos
  de linhas `messaging:`/`automation:` (nunca reexecuta) e avisa o tópico.
- Quem precisa: Hefesto (código inerte sem a env).
- v0.22.5: **ADAPTAR**. O que mudou upstream:
  - `settle_recovered_unattended` (novo, GW-10 upstream) já liquida no boot linhas
    `automation:`/cron/webhook/kanban como `interrupted` com aviso no transcript — essa metade do
    nosso recurso fica redundante (inofensiva: quando nosso código roda, essas linhas já saíram de
    `unknown`).
  - Para linhas humanas, a v0.22.5 só oferece `/discard` manual no tópico (o aviso de pausa agora
    diz "Send /discard ..."). Para o Hefesto (tópicos sem operador presente) o automático continua
    necessário.
  - `recover_gateway_native_sessions` agora delega a `recover_profile_native_sessions(runner,
    authority)`, que também é chamado pelo reconcile de perfis servidos a quente
    (`recover_served_native_rows`). Resolução: ganchos Dize só no boot, depois de
    `recover_profile_native_sessions`, dentro de `owner_scope(authority)`, via
    `_dize_startup_recovery` (falha macia, mesmos logs/verdicts).
  - APIs usadas conferidas na v0.22.5: `authority.resolve_unknown(actor, ref, admission_id,
    generation)` (agora delega a `resolve_unknown_as_owner`), `Principal`, `SessionRef`,
    `list_session_admissions(db, session_id=, pending_only=)`, `replace_source`,
    `recover_native_sessions` ainda faz `sessions.setdefault(...)` antes de levantar
    `unknown_execution` (então `authority.sessions.get(sid)` acha a fonte).
  - Opcional: trocar para `resolve_unknown_as_owner(ref, id, gen, notice=...)` para também gravar
    um fechamento no transcript, como o upstream faz para automação.
- Conflito: sim → `resolved/gateway/run_runtime.py`.
- Teste comportamental: com a env ligada, matar o gateway (`kill -9`) no meio de um turno de
  tópico e subir: log "Dize lost-turn recovery: discarded 1 lost turn(s)"; o tópico recebe o aviso
  "⚠️ O Hermes reiniciou no meio da tarefa..."; a próxima mensagem roda sem "conversation is paused".
- Risco: médio (sem ele: tópico pausado até alguém mandar `/discard`).

## GW-09 — Retomar turno cortado por reinício planejado (opt-in)

- Arquivos/funções: `gateway/dize_lost_turn_recovery.py::resume_restart_interrupted_turns`,
  `_cut_by_previous_owner`; mesma chamada em `run_runtime`.
- Testes Dize: `tests/gateway/test_dize_restart_resume.py`.
- Motivo (920144c482): em 0.21 o turno cortado por reinício era retomado via `resume_pending`;
  desde 0.22 ele fecha `interrupted` e `_schedule_resume_pending_sessions` pula toda rota com
  admissões, então o tópico para. Com `HERMES_DIZE_RESUME_INTERRUPTED_TURNS=continue` (Hefesto),
  admite UM turno de automação com a nota de recuperação.
- Quem precisa: Hefesto.
- v0.22.5: **ADAPTAR** (mesmo ponto do GW-08). Upstream continua pulando rotas com admissões
  (`run_startup._schedule_resume_pending_sessions`: "Canonical admissions own restart decisions"),
  então o recurso continua necessário. Conferido: `admit_route_wake`, `admission_result`,
  `build_resume_recovery_note`, `_auto_continue_freshness_window`, `_is_fresh_gateway_interruption`
  existem com a mesma assinatura; `_INTERRUPT_REASON_GATEWAY_SHUTDOWN/RESTART` continuam
  "Gateway shutting down"/"Gateway restarting". Rodar só no boot evita reavaliar no reconcile a
  quente.
- Teste comportamental: com turno longo num tópico, `/restart` com drenagem curta → após subir,
  log "Dize restart recovery: resuming <sid> (... mode=continue)" e o tópico recebe um turno que
  continua a tarefa sem repetir ferramentas já registradas; mensagem do usuário não é reexecutada.
- Risco: **alto** (sem ele, cada reinício deixa tópicos ativos parados até alguém escrever).

## GW-10 — Backport v0.22.2 do host-record após reboot

- Arquivos: `gateway/host_rendezvous.py`, `tests/gateway/test_host_rendezvous.py` (ec60f2ed9b).
- Motivo: 0.22.1 confundia PID reciclado com gateway vivo após reboot e recusava subir (#133744).
- Quem precisa: frota.
- v0.22.5: **REMOVER**. `git diff fork/hermes-hefesto v0.22.5 -- gateway/host_rendezvous.py` só
  acrescenta (`host_lock_is_held`, `record_names_live_owner`, guarda em `liveness_is_proven` para
  registro sem create/start time, `discard_dead_record` segurando o lock, `make_user_dir`). No probe
  o arquivo e o teste ficaram idênticos à v0.22.5.
- Teste comportamental: reboot do VPS com `systemctl` subindo o gateway → sobe na primeira
  tentativa, sem "another gateway is running (pid ...)".
- Risco: baixo.

## GW-11 — Metadados de mídia Telegram do Hefesto na admissão nativa

- Arquivos/funções: `gateway/session_envelope.py` — `_DIZE_TELEGRAM_METADATA`,
  `_DIZE_TELEGRAM_METADATA_MAX_BYTES`, ramo Telegram em `_native_metadata`.
- Testes Dize: `tests/gateway/test_dize_telegram_media_metadata.py`.
- Motivo (d50f899c9e): o plugin `hefesto-execution-control` carimba
  `hefesto_telegram_document/_media/hefesto_native_batch/hefesto_recover_documents` antes da
  admissão; a v0.22 só aceitava metadados WhatsApp → fotos, álbuns e documentos falhavam
  `invalid_params`.
- Quem precisa: **só Hefesto** (é a única diferença entre `fork/hermes` e `fork/hermes-hefesto`).
- v0.22.5: **ADAPTAR** (trivial). Upstream acrescentou `_MEDIA_FIELDS` na mesma linha e
  reestruturou a validação em `_event_clause` → `_native_metadata(event) is None` continua sendo o
  portão; `snapshot_native` continua gravando `envelope['event']['metadata']` e `restore_native`
  devolve via `**envelope['event']`. Sem o patch, a v0.22.5 ainda responde no tópico
  "⚠️ Hermes refused this message (invalid_params)".
- Conflito: sim → ambos (`resolved/gateway/session_envelope.py`).
- Teste comportamental: mandar uma foto, um álbum de 3 fotos e um PDF num tópico → os três geram
  turno; nenhum "Native admission refused: invalid_params (metadata)" no log; reiniciar com uma
  mídia na fila → replay mantém as chaves.
- Risco: **alto** (mídia é rotina nos tópicos de cliente).

## GW-12 — Gateway sem multiplex serve/agenda só o perfil de lançamento

- Arquivos/funções: `gateway/run.py::_multiplex_profile_homes`.
- Testes Dize: `tests/gateway/test_dize_single_profile_homes.py`,
  `tests/hermes_cli/test_gateway_multiplex_mode.py` (parcial).
- Motivo (b81c55f2e9): sem reserva de multiplex, a v0.22 usava todos os diretórios de
  `profiles/` → um gateway `--profile hefesto` dispararia cron, roteamento de log e injeção de
  plugin de perfis irmãos de outros hosts.
- Quem precisa: frota.
- v0.22.5: MANTER. Upstream não mudou a função; `_cron_tick_profile_homes` ainda soma
  `_multiplex_profile_homes`; o novo `_cron_profile_gate` só se abstém se OUTRO gateway no mesmo
  host for dono do perfil, o que não protege diretórios copiados de outros hosts.
  `multiplex_profiles` continua `Optional[bool] = None`.
- Teste comportamental: no Hefesto, com um diretório de perfil irmão contendo cron, observar 2
  ciclos do ticker: nenhum job do irmão roda; `hermes gateway status` lista só `hefesto`.
- Risco: médio.

## GW-13 — Hook de plugin `gateway_housekeeping_tick`

- Arquivos/funções: `gateway/run.py::_start_gateway_housekeeping` (a cada 10 ticks chama
  `invoke_hook("gateway_housekeeping_tick", tick_count=, interval_seconds=)` via
  `_housekeeping_chore`); acoplado: `hermes_cli/plugins.py` (`VALID_HOOKS`, fora do escopo).
- Testes Dize: `tests/gateway/test_gateway_housekeeping_plugin_hook.py`.
- Motivo: linha 0.21 (existia antes dos hotfixes). Dá um relógio periódico a plugins.
- Quem precisa: frota (algum plugin; INCERTO qual — não vi os plugins).
- v0.22.5: MANTER. `_housekeeping_chore(label, fn, *args, **kwargs)` ainda aceita argumentos; o
  laço é o mesmo.
- Teste comportamental: plugin com o hook registra log a cada `10 × interval`.
- Risco: baixo (INCERTO o que o plugin faz com ele).

## GW-14 — `_compressed_summary` preservado no replay

- Arquivos/funções: `gateway/run.py::_build_replay_entry`.
- Testes Dize: `tests/agent/test_authorized_task_continuity.py` (fora do escopo, usa a flag).
- Motivo: hotfix do Hefesto (3336def46a): o seletor de compressão exige a flag interna; sem ela o
  resumo replayed parecia mensagem comum.
- v0.22.5: **REMOVER**. `_build_replay_entry` da v0.22.5 faz
  `entry.update(scaffolding_replay_fields(msg))` e `agent/message_metadata.py::scaffolding_replay_fields`
  devolve `{"_compressed_summary": True} if msg.get("_compressed_summary")`. O probe aplicou as
  duas (duplicado inofensivo); retirar o hunk Dize.
- Teste comportamental: sessão de tópico compactada, reiniciar → no próximo turno o resumo é
  reconhecido como resumo (sem nova compactação imediata nem "resumo citado" como pedido do usuário).
- Risco: baixo.

## GW-15 — `dm_allow_from` na ponte config.yaml → extra

- Arquivo: `gateway/config_loader.py` (`_SHARED_KEYS`).
- Testes: `tests/gateway/test_config.py`, `test_telegram_group_gating.py`,
  `test_telegram_auth_check.py`, `test_discord_*` (esses cobrem também os adapters, fora do escopo).
- Motivo: linha 0.21; permite allowlist só de DM separada da de grupo. Só tem efeito com os
  patches dos adapters Telegram/Discord (outro inventário).
- v0.22.5: MANTER (upstream não lê `dm_allow_from` para Telegram/Discord).
- Teste comportamental: usuário fora de `dm_allow_from` mas em `group_allow_from` → DM negada,
  mensagem no grupo aceita.
- Risco: baixo para tópicos.

## GW-16 — `send_message` Telegram avulso

- Arquivo/funções: `tools/send_message_senders.py::_telegram_bot` (sem proxy usa
  `HTTPXRequest(transport=TelegramFallbackTransport(SEED_FALLBACK_IPS))`), `_send_telegram`
  (resultado com `thread_id`/`telegram_chat_id`; `finally: await bot.request.shutdown()`).
- Testes Dize: `tests/tools/test_send_message_telegram_proxy.py`
  (incl. `test_native_transport_does_not_retry_ambiguous_delivery`), `test_send_message_tool.py`.
- Motivo: linha 0.21. Mesma resiliência de rede do gateway para envios fora dele; não vazar
  clientes HTTP; o chamador saber o tópico real.
- v0.22.5: MANTER. O arquivo perdeu 400 linhas upstream (senders de outras plataformas saíram), mas
  a parte Telegram é a mesma; `TelegramFallbackTransport`, `SEED_FALLBACK_IPS` e
  `_success(**fields)` existem.
- Teste comportamental: `send_message` para `telegram:<chat>:<thread>` sem gateway vivo → chega no
  tópico, resultado traz `thread_id`; sem "Unclosed client" no log.
- Risco: baixo.

## GW-17 — Texto dos avisos de delegação

- Arquivos/funções: `tools/process_registry_notifications.py` (`_artifact_pointer_lines`,
  `_event_snapshot_lines`, `_terminal_failure_lines`, textos de `_format_task_failure_notice`,
  `_format_batch_delegation`, `_format_async_delegation`); `tools/delegate_tool_progress.py`
  (`format_subagent_failure_line` aceita enum e trata `content_policy_blocked`).
- Testes Dize: `tests/tools/test_async_batch_task_failure_notice.py` (texto Hefesto). Os ponteiros
  de arquivo não têm teste direto.
- Motivo: hotfixes do Hefesto (3336def46a): o pai recebe caminhos exatos escritos com sucesso pelo
  filho (sem inventar), hora do evento (não é prova de que o fan-out ainda roda) e instrução de não
  contornar recusa de política.
- v0.22.5: MANTER. Upstream só mexeu em títulos/heartbeat; `tool_trace` com
  `input_summary.targets.path` e `status` ainda é produzido (`delegate_tool_child_run`,
  `delegate_tool_results._input_summary`). Novidade: a v0.22.5 redige o resultado do filho
  (`redact_child_result`) antes de enfileirar — caminhos continuam, mas um caminho com segredo
  aparente pode vir redigido (aí cai em "omitted").
- Teste comportamental: filho async que grava `/root/x/a.md` → o aviso no pai lista
  `- "/root/x/a.md"` e a linha "Event snapshot time: ...".
- Risco: baixo.

## GW-18 — Dreno sem filtro nunca consome async_delegation; dono no CLI por linhagem

- Arquivos/funções: `tools/process_registry.py::_owns_event` (`return not is_async_delegation`
  em vez de só para `restored`); `hermes_cli/cli_process_notifications.py`
  (`_owns_async_delegation_event`, desvio em `_owns_process_notification`). Acoplado (fora do
  escopo): `SessionDB.is_compression_ancestor` público em `hermes_state_titles.py` (o probe tem).
- Testes Dize: `tests/tools/test_process_registry.py`
  (`test_drain_notifications_no_filter_requeues_async_delegation_fail_closed`),
  `tests/tools/test_delegate_control_actions.py` (ajuste), `tests/test_cli_async_delegation_ownership.py`,
  `tests/cli/test_single_query_async_capability.py`.
- Motivo (linha 0.21, 7938692d00): um dreno sem session_key (CLI recém-aberto) podia pegar o
  resultado de delegação de outra sessão.
- v0.22.5: MANTER. Upstream mantém `return not (is_async_delegation and evt.get("restored"))`; a
  novidade `startup_replay` não altera esse ramo. O gateway não usa `drain_notifications` (usa o
  próprio dreno), então o efeito é no CLI/single-query.
- Teste comportamental: duas sessões CLI, delegação async na A → só A recebe; B nunca.
- Risco: baixo para tópicos.

## GW-19 — `_reset_for_tests` espera workers

- `tools/async_delegation.py::_reset_for_tests` (`shutdown(wait=True, cancel_futures=True)`);
  teste `test_reset_for_tests_prevents_late_completion_queue_writes`. Só testes. MANTER.

---

## Símbolos internos usados pelos plugins do Hefesto (v0.22.5)

| símbolo | v0.22.5 | observação |
|---|---|---|
| `gateway.session_ingress.executing_admission` | igual (`ContextVar('executing_admission', default=False)`, set/reset em `execute_admission`) | `execute_admission` agora: chama `note_if_unviewed` antes (só `local_automation_v1`), marca `event._claimed_owner` em rota de chat, usa `admit_managed`, novo ContextVar `viewer_turn`. `admit_message` agora devolve texto de recusa para `invalid_params` com campo (relevante a GW-11) |
| tabela `delivery_obligations` / `gateway.delivery_ledger.compute_obligation_id` | igual (`sha256(f"{session_key}|{message_ref}|{content}")[:24]`; `record_obligation` mesma assinatura) | `delivery_ledger.py` sem diff entre v0.22.1 e v0.22.5 |
| `gateway.run._gateway_runner_ref` | igual (weakref, atribuído no `__init__` do runner) | — |
| `gateway.drain_control.drain_requested(*, home=None)` | igual | novo `restart_wait` (`drain_reason`) durante a espera pós-turno: **não** cria marcador de drain; `drain_requested()` continua False nessa janela |
| `gateway.wake.admit_internal_event(adapter, event)` | igual | `WakeNotAccepted(message="...", *, reason=None)` ganhou `reason`/`permanent`; construção posicional continua válida. Recusas `not_found`/`invalid_params` agora são permanentes (descartadas após 8 tentativas no dreno) |
| `tools.async_delegation.has_live_for_session(session_key="", origin_ui_session_id="", parent_session_id="")` | igual | — |
| `tools.async_delegation.interrupt_for_session(..., reason="session_end")` | igual | novos `refuse_for_session`/`allow_for_session` (só usados no fechamento de viewer local) |
| `tools.process_registry.process_registry` | existe | ver GW-18 |
| `gateway.response_filters.is_machinery_display_kind`, `gateway.cgroup_cleanup._own_cgroup_path`, `gateway.platforms.base.cache_media_bytes`, `gateway.session_context.get_session_env`, módulos `gateway.agent_cache_pressure`, `tools.clarify_gateway`, `gateway.slash_commands_session` | existem | importados pelos plugins (lista `scratchpad/imports.txt`); só conferi existência, não semântica. O plugin importa o módulo `gateway.slash_commands_session` inteiro — se ele substitui métodos de `GatewaySessionCommandsMixin`, a v0.22.5 acrescentou `_handle_discard_command`/`discard_lost_turns` e reescreveu `/retry` (`_retry_in_admission`): INCERTO sem ver o plugin |

Mudanças upstream que afetam os tópicos mesmo sem patch Dize:
- `/discard` no Telegram e aviso de pausa novo (GW-08).
- Recusas de notificação com backoff (`_requeue_refused_watch_event`) e descarte só de recusa
  permanente; no ledger durável, recusa permanente gasta tentativa (`refused = not exc.permanent`).
- `admit_route_wake`/`deliver_wake` passam `reason` do RuntimeStoreError.

## Testes executados (worktree `scratchpad/wt-gateway`: v0.22.5 + delta do probe + resoluções)

- Dize/gateway: `test_dize_automation_delegation_metadata`, `test_dize_lost_turn_recovery`,
  `test_dize_restart_resume`, `test_dize_single_profile_homes`, `test_dize_telegram_media_metadata`,
  `test_gateway_housekeeping_plugin_hook`, `test_restart_drain`, `test_producer_owner_continuity`,
  `test_pre_gateway_dispatch` → **67 passed, 2 skipped**.
- Notificações/ferramentas: `test_completion_delivery`, `test_background_process_notifications`,
  `test_config`, `test_host_rendezvous`, `test_telegram_auth_check`,
  `test_telegram_callback_auth_fail_closed`, `test_telegram_group_gating`,
  `tests/tools/test_async_delegation.py`, `test_process_registry.py`,
  `test_delegate_control_actions.py`, `test_async_batch_task_failure_notice.py`,
  `test_send_message_telegram_proxy.py`, `test_send_message_tool.py`,
  `tests/test_cli_async_delegation_ownership.py`, `tests/cli/test_single_query_async_capability.py`
  → **488 passed, 5 skipped** (skips por plataforma).
- Regressão ampla (124 arquivos de tests/gateway que tocam os módulos alterados + testes de
  delegação/process_registry): ver adendo abaixo.
- Prova do defeito de GW-07: snapshot com `_records = {'x': {'status': 'timeout'}}` devolve
  `delegation_unknown`.

### Adendo — regressão ampla

180 arquivos (124 de `tests/gateway` que referenciam os módulos alterados + `tests/tools/test_async_delegation*`,
`test_process_registry*`, `test_delegate*`, `tests/test_cli_async*`): **2052 passed, 12 skipped, 3 failed** (18 min).
As 3 falhas não vêm do delta:
- `test_multiplex_routing_authz.py::test_completion_preflight_runs_in_target_profile_scope` e
  `test_process_registry.py::TestSigkillEscalation::test_entire_tree_is_sigkilled_not_just_parent`:
  passam isoladas na worktree mesclada (instáveis sob carga).
- `test_process_registry_list_exit.py::test_list_reconciles_real_exit_without_consuming_owned_result`:
  falha igual na v0.22.5 pura (o subprocesso do teste usa o venv, que não tem pytest; é artefato do ambiente).

Worktree usada: `scratchpad/wt-gateway` (v0.22.5 + delta do probe + `scratchpad/resolved/*`), sem commit.
O branch `probe/v0.22.5-apply` não foi alterado.
