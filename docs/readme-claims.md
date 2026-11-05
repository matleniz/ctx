# Claims and the tests that prove them

Unit tests run offline (`python3 -m unittest discover -s tests`). The journey `step_*` functions of
`tests/integration/run_readme.py` use a real `claude -p` on a throwaway test tree.
`tests/test_readme.py` checks that every test named below exists. Rows for other pages are labelled `page: section`.

| Section | Claim | Test |
|---|---|---|
| Top | A picture (light and dark SVG, no script, no external reference) shows state going up, rules coming down, rewrite after the session | `test_files_exist_and_are_referenced_by_the_picture`, `test_svgs_are_self_contained` |
| Top | The agent updates Status, Next and summary itself (adopted with no model call, through the same validator); the model rewrite is the fallback | `test_state_written_by_the_agent_is_adopted_without_a_model_call`, `test_invalid_agent_state_falls_back_to_the_model`, `test_project_injection_asks_the_agent_to_write_its_state_container_does_not` |
| Top | A session also receives its node's parent line, `parent <name>: <summary>` (<= 300 chars, filtered), never the parent's Subfolders lines or siblings, nothing for a root node | `test_child_gets_one_parent_line_without_subfolders_or_siblings`, `test_parent_line_is_filtered_and_capped`, `test_computed_parent_summary_is_not_injected`, `test_root_node_has_no_parent_line` |
| Top | The rewriter's output is rejected (retry once) when a line describes structure; the prompt asks for state with file paths and `decision:` lines | `test_structure_lines_rejected_state_lines_kept`, `test_parse_model_output_rejects_structure_in_any_part`, `test_prompt_states_the_rules` |
| 5 minutes | `./install.sh` exists and is executable; links `~/.local/bin/ctx`, idempotent | `test_install_block`, `test_install_hooks_and_uninstall_leave_the_configs_as_they_were` |
| 5 minutes | `ctx install-hooks <cli>` works for claude, cursor, agy and shows a diff first | `test_clients_named_in_readme_are_supported`, `test_install_hooks_dry_run_does_not_write`, `test_install_hooks_confirmation_refused` |
| 5 minutes | In a folder without a node the agent is told to ask once; `decline` stops the question | `test_subfolder_without_node_gets_discovery_not_absorption`, `test_declined_is_silent`, `step_discovery_yes` |
| 5 minutes | After "yes" the node is created and the next session receives it | `step_discovery_yes`, `step_work_and_sync` |
| Commands | There are 7 commands (`decline` and `hook` are internal, hidden from `--help`); the listed ones are exactly the real ones | `test_command_list_is_the_real_one` |
| Commands | Every command and flag in the README exists in `--help` | `test_every_command_and_flag_exists` |
| Commands | `ctx --version` prints the version | `test_version_flag_prints_the_version` |
| Top | A node is a folder whose `CONTEXT.md` `ctx init` wrote on this machine (header `ctx: 1`); a cloned or committed one is not trusted | `test_new_nodes_carry_the_marker`, `test_foreign_context_is_not_a_node_anywhere`, `test_cloned_marker_file_is_not_trusted_nor_injected` |
| 5 minutes | The hooks call `<clone>/ctx.py` by its path (keep the clone in place, update with `git pull`) | `test_ctxignore_and_clone_location_are_documented` |
| 5 minutes | Folders matching `~/.ctxignore` are never nodes | `test_init_refuses_ignored_and_outside`, `test_ctxignore_and_clone_location_are_documented` |
| Commands | `show` prints the count of injected characters | `test_show_reports_characters` |
| Commands | `show --tree` lists nodes and summaries | `test_tree_lists_nodes` |
| Commands | `undo` restores the previous version | `test_undo_restores_previous`, `step_undo` |
| reference: Log and doctor | `log` shows the last lines, a session or the failures; `doctor` shows pending/abandoned sessions and last sync per node | `test_ctx_log_filters`, `test_doctor_shows_pending_abandoned_and_last_sync` |
| reference: Log and doctor | The log has one line per event, a tool counter at session end, no line per tool call | `test_session_lifecycle_is_logged_one_line_per_event_with_a_tool_counter` |
| reference: Log and doctor | `doctor` detects caps, ghosts, orphans, failed syncs | `test_doctor_ok_then_detects`, `test_doctor_reports_failed_sync_and_ghost` |
| 5 minutes | The commands `install.sh` suggests (`ctx init --no-absorb ~`, `ctx init <project>`) leave `ctx doctor` clean: an empty container and a node under a non-node folder are not problems (the latter is an `info:` line) | `test_suggested_commands_then_doctor_exits_0` |
| reference: Nodes | `init` absorbs non-node subfolders by default; `--no-absorb` for containers | `test_new_nodes_default_to_absorb_true_and_containers_opt_out`, `test_project_node_absorbs_subfolder_and_never_asks_there` |
| Uninstall | `install-hooks --remove` removes the hooks, keeps `CONTEXT.md`, leaves the link to the user | `test_uninstall_roundtrip_byte_identical_dry_run_and_link_kept`, `test_uninstall_keeps_context_files`, `step_uninstall` |
| reference: Files | Cap numbers quoted in reference.md are the code constants | `test_numbers_are_the_constants`, `test_section_names_are_the_real_ones` |
| reference: Files | `CONTEXT.md` is excluded from git | `test_git_exclude_and_clean_status`, `test_init_excludes_from_git_and_session_flow_writes_ctx_dir` |
| reference: Files | `~/.ctx` holds log, declined, sessions, history, state; history keeps 10 versions | `test_init_excludes_from_git_and_session_flow_writes_ctx_dir`, `test_history_capped_at_10` |
| reference: Files | Notes zone is never rewritten | `test_notes_zone_preserved_and_hidden_from_model` |
| Uninstall | `ctx install-hooks --remove` restores the hook configs (same JSON; byte for byte when ctx wrote their format) | `test_uninstall_roundtrip_byte_identical_dry_run_and_link_kept`, `step_uninstall` |
| reference: Files | Hook config paths of each CLI | `test_clients_named_in_readme_are_supported` |
| how-it-works: How ctx works | Three levels are updated after a session | `step_work_and_sync`, `test_sync_rewrites_node_and_bubbles_up` |
| how-it-works: Isolation | A session receives its nearest node only, never a sibling; the rewriter is given no sibling file (isolation by inputs, no output filter) | `test_isolation_sibling_unchanged`, `test_injects_nearest_node`, `step_sibling_isolated`, `test_rewriter_input_is_exactly_the_session_reports_no_sibling_filter` |
| reference: Files | Injection is capped at 9500 characters | `test_injection_never_exceeds_cap` |
| reference: Files | Parent keeps its own summary; 8 full entries, then 80 characters, then a count | `test_rewritten_node_keeps_its_own_summary`, `test_subs_rule_8_full_80_chars_plus_n`, `test_bulk_children_bounded` |
| how-it-works: Guards | The rewrite is `claude -p` (haiku default), no tools, no hooks | `test_rewriter_command_is_claude_p_without_tools_or_hooks`, `test_sync_inner_env_in_rewriter_command` |
| reference: Configuration and environment | `~/.ctx/config` (`rewriter` = `claude` only, `model`); an unsupported rewriter is reported with a clear message | `test_config_parsing_comments_quotes_unknown_keys_and_bad_lines`, `test_env_ctx_rewriter_is_gone`, `test_other_rewriter_names_give_a_clear_message`, `test_doctor_reports_an_unsupported_rewriter`, `test_doctor_shows_the_rewriter_and_checks_the_command_exists` |
| how-it-works: Guards | Secrets and instruction-like lines are filtered on a best-effort, pattern basis; invalid output keeps the old file | `test_secret_filtered_in_input_and_output`, `test_instruction_lines_dropped`, `test_instruction_lines_are_dropped`, `test_state_lines_pass`, `test_env_style_keys_and_url_credentials`, `test_pem_block_spanning_lines_is_removed_whole`, `test_invalid_output_keeps_old_file` |
| reference: Configuration and environment | `CTX_OFF=1` disables the hooks | `test_ctx_off_makes_hooks_silent`, `test_ctx_off_values` |
| how-it-works: Guards | `log` and `doctor` output cannot be forged by control characters in logged or hand-written lines | `test_log_strips_control_chars_and_cannot_forge_lines_or_pairs`, `test_log_and_doctor_output_sanitise_old_or_hand_written_lines` |
| how-it-works: Guards | An abandoned sync's cause is logged inside the data frame, filtered and redacted | `test_abandoned_cause_is_inside_the_data_frame_filtered_and_redacted` |
| reference: Files | The log is rotated at 1 MB without losing lines under concurrent writers | `test_log_rotation_with_concurrent_writers_loses_nothing`, `test_log_rotation_sessions_purge_history_cap_and_size` |
| reference: Log and doctor | `doctor --fix` skips folders whose parent is unreachable; moved or deleted nodes are handled | `test_doctor_fix_skips_folders_whose_parent_is_unreachable`, `test_moved_renamed_deleted_nodes_and_doctor_fix` |
| reference: Nodes | A `CONTEXT.md` with `ctx: 1` that ctx did not write on this machine (clone, commit) is not trusted nor injected; `ctx init` adopts it keeping its Notes; a moved node stays a node | `test_cloned_marker_file_is_not_trusted_nor_injected`, `test_init_adopts_a_cloned_node_keeping_its_notes`, `test_moved_node_is_still_a_node` |
| how-it-works: When things break | An edit made during a sync is not overwritten (the sync is redone from it); an edit made outside any session is the base, Status included | `test_edit_during_the_model_call_is_redone_from_the_new_file`, `test_light_sync_keeps_a_hand_edited_status`, `test_agent_edit_emptying_decisions_is_settled_at_once` |
| how-it-works: Guards | `~/.ctx` is private (0700, files 0600); `CONTEXT.md` keeps its mode | `test_state_is_private_and_context_md_keeps_its_mode` |
| reference: Configuration and environment | A model name starting with a dash is rejected | `test_config_rejects_dash_model` |
| how-it-works: When things break | After a killed, cut, slow or failing sync the old file is kept (no partial write): the group is killed on timeout, abandoned syncs are visible and retried by hand, hand edits during a sync win, hooks always exit 0 | `test_kill_minus_9_of_the_sync_group_during_the_model_call`, `test_cut_between_node_rewrite_and_parent_rebuild`, `test_cut_between_session_end_hook_and_popen`, `test_sleep_and_wsl_stop`, `test_model_call_impossible_four_causes`, `test_abandon_is_visible_and_retry_by_hand`, `test_timeout_kills_the_whole_process_group`, `test_external_write_is_the_base_and_conflict_files_are_ignored`, `test_hand_edit_during_a_sync`, `test_missing_ctx_dir_is_recreated_and_hooks_never_crash`, `test_read_only_home_hooks_exit_0`, `test_disk_full_hook_exits_0_and_logs`, `test_slow_hook_gives_up_before_the_cli_delay` |
| Uninstall | The journey ends with a clean uninstall in a temporary HOME | `step_uninstall` |
| Limits | The rewrite needs `claude`; cursor and agy are hook clients only (no rewriter) | `test_no_config_file_is_fine`, `test_install_hooks_cursor_prints_no_rewriter_line` |
| reference: Behaviour details | agy sessions (no end hook) are caught up by a later session start once over | `test_agy_post_tool_use_collects_only_edit_tools_and_answers_empty_object`, `test_catch_up_finds_crashed_session` |
| reference: Behaviour details | A session without its end marker is over after 6 hours without a hook | `test_numbers_are_the_constants`, `test_a_session_is_over_when_ended_or_idle_for_hours` |
| reference: Behaviour details | A session without file changes gets a sync-light by code (decision: and next: lines), no model call | `test_decision_and_next_lines_are_applied_without_a_model_call`, `test_light_never_changes_status_or_summary`, `test_instruction_shaped_next_line_is_dropped` |
| how-it-works: Guards | Two syncs of one node wait for each other; a lock wait that runs out is retried, not counted as a failure | `test_a_second_sync_of_a_node_waits_for_the_first`, `test_lock_wait_runs_out_busy_not_failed`, `test_failed_sync_is_replayed_from_the_session_offsets` |
| Limits | Linux, WSL, macOS only (`fcntl`); opencode unsupported | `test_platform_limit_fcntl`, `test_clients_named_in_readme_are_supported` |
| Measured | The README names the versions measured and links the benchmark documents | `test_length_and_links` |
| Privacy | `~/.ctx` is private (0700, files 0600); what the rewrite receives is filtered for secrets | `test_state_is_private_and_context_md_keeps_its_mode`, `test_secret_filtered_in_input_and_output` |
| Privacy | `private: true` shows `(private)` in the parent instead of the summary | `test_private_not_bubbled_and_instruction_summary_filtered`, `test_private_children_skipped` |
| License | The project is MIT-licensed (`LICENSE` file) | `test_license_is_mit_with_owner` |
| reference: Nodes | Only files ctx wrote on this machine are nodes; any other `CONTEXT.md` is foreign: ignored, listed by `doctor` as `info:` (exit 0), never written by `doctor --fix`, and by `init` only when it carries `ctx: 1` | `test_foreign_context_is_not_a_node_anywhere`, `test_marker_less_file_in_ctx_shape_is_foreign_too` |
| reference: Nodes | Only summary + 4 known sections are injected | `test_injection_allow_list` |

Claims proven only by the integration runs (a real `claude -p`, not run by the offline suite): the discovery question
and "yes" creating a node (`step_discovery_yes`), the three-level update after real work (`step_work_and_sync`), sibling
isolation with a real model (`step_sibling_isolated`) and the real-CLI end of the journey (`step_uninstall`). Their
offline counterparts above check the same code paths with a fake rewriter, not the model's behaviour.
The install journey from a clean copy in a temporary HOME (no model) is a unit test (`tests/test_install_journey.py`).

Not covered by a unit test, therefore not claimed: hook behaviour inside each real CLI beyond what
the integration runs measured (see [clients.md](clients.md)).
