# Claims and the tests that prove them

Unit tests run offline (`python3 -m unittest discover -s tests`). The journey `step_*` functions of
`tests/integration/run_readme.py` use a real `claude -p` on a throwaway test tree.
`tests/test_readme.py` checks that every test named below exists and that every README section has a row
(the top picture has its own `Top` row). Rows for other pages are labelled `page: section`.

| Section | Claim | Test |
|---|---|---|
| Top | A picture (light and dark SVG, no script, no external reference) shows state going up, rules coming down, rewrite after the session; its alt text says so | `test_files_exist_and_are_referenced_by_the_picture`, `test_alt_names_the_three_movements`, `test_svgs_are_self_contained` |
| Top | A session also receives one `parent <name>: <summary>` line (<= 300 chars, filtered), never the parent's Subfolders lines or siblings, nothing for a root node | `test_child_gets_one_parent_line_without_subfolders_or_siblings`, `test_parent_line_is_filtered_and_capped`, `test_computed_parent_summary_is_not_injected`, `test_root_node_has_no_parent_line` |
| Top | The rewriter's output is rejected (retry once) when a line describes structure; the prompt asks for state with file paths and `decision:` lines | `test_structure_lines_rejected_state_lines_kept`, `test_parse_model_output_rejects_structure_in_any_part`, `test_prompt_states_the_rules` |
| 5 minutes | `./install.sh` exists and is executable; links `~/.local/bin/ctx`, idempotent | `test_install_block`; clean-copy run `tests/integration/run_install.py` |
| 5 minutes | `ctx install-hooks <cli>` works for claude, cursor, agy and shows a diff first | `test_clients_named_in_readme_are_supported`, `test_install_hooks_dry_run_does_not_write`, `test_install_hooks_confirmation_refused` |
| 5 minutes | In a folder without a node the agent is told to ask once; `decline` stops the question | `test_subfolder_without_node_gets_discovery_not_absorption`, `test_declined_is_silent`, `step_discovery_yes` |
| 5 minutes | After "yes" the node is created and the next session receives it | `step_discovery_yes`, `step_work_and_sync` |
| Commands | There are 7 commands (`decline` and `hook` are internal, hidden from `--help`); the listed ones are exactly the real ones | `test_command_list_is_the_real_one` |
| Commands | Every command and flag in the README exists in `--help` | `test_every_command_and_flag_exists` |
| Commands | `ctx --version` prints the version | `test_version_flag_prints_the_version` |
| Top | A node is a folder whose `CONTEXT.md` carries the `ctx: 1` header written by `ctx init` | `test_new_nodes_carry_the_marker`, `test_foreign_context_is_not_a_node_anywhere` |
| 5 minutes | The hooks call `<clone>/ctx.py` by its path (keep the clone in place, update with `git pull`) | `test_ctxignore_and_clone_location_are_documented` |
| 5 minutes | Folders matching `~/.ctxignore` are never nodes | `test_init_refuses_ignored_and_outside`, `test_ctxignore_and_clone_location_are_documented` |
| Commands | `show` prints the count of injected characters | `test_show_reports_characters` |
| Commands | `show --tree` lists nodes and summaries | `test_tree_lists_nodes` |
| Commands | `undo` restores the previous version | `test_undo_restores_previous`, `step_undo` |
| reference: Log and doctor | `log` filters the log by node, session, age, failures; `doctor` shows pending/abandoned sessions and last sync per node | `test_ctx_log_filters`, `test_doctor_shows_pending_abandoned_and_last_sync` |
| reference: Log and doctor | The log has one line per event, a tool counter at session end, no line per tool call | `test_session_lifecycle_is_logged_one_line_per_event_with_a_tool_counter` |
| reference: Log and doctor | `doctor` detects caps, ghosts, orphans, failed syncs | `test_doctor_ok_then_detects`, `test_doctor_reports_failed_sync_and_ghost` |
| 5 minutes | The commands `install.sh` suggests (`ctx init --no-absorb ~`, `ctx init <project>`) leave `ctx doctor` clean: an empty container and a node under a non-node folder are not problems (the latter is an `info:` line) | `test_suggested_commands_then_doctor_exits_0` |
| reference: Nodes | `init` absorbs non-node subfolders by default; `--no-absorb` for containers | `test_new_nodes_default_to_absorb_true_and_containers_opt_out`, `test_project_node_absorbs_subfolder_and_never_asks_there` |
| Uninstall | `install-hooks --remove` removes the hooks, keeps `CONTEXT.md`, leaves the link to the user | `test_uninstall_roundtrip_byte_identical_dry_run_and_link_kept`, `test_uninstall_keeps_context_files`, `step_uninstall` |
| reference: Files | Cap numbers quoted in reference.md are the code constants | `test_numbers_are_the_constants`, `test_section_names_are_the_real_ones` |
| reference: Files | `CONTEXT.md` is excluded from git | `test_git_exclude_and_clean_status`, `test_init_excludes_from_git_and_session_flow_writes_ctx_dir` |
| reference: Files | `~/.ctx` holds log, declined, sessions, history, state; history keeps 10 versions | `test_init_excludes_from_git_and_session_flow_writes_ctx_dir`, `test_history_capped_at_10` |
| reference: Files | Notes zone is never rewritten | `test_notes_zone_preserved_and_hidden_from_model` |
| Uninstall | `ctx install-hooks --remove` restores the hook configs byte for byte | `test_uninstall_roundtrip_byte_identical_dry_run_and_link_kept`, `step_uninstall` |
| reference: Files | Hook config paths of each CLI | `test_clients_named_in_readme_are_supported` |
| how-it-works: How ctx works | Three levels are updated after a session | `step_work_and_sync`, `test_sync_rewrites_node_and_bubbles_up` |
| how-it-works: Isolation | A session receives its nearest node only, never a sibling; the rewriter is given no sibling file (isolation by inputs, no output filter) | `test_isolation_sibling_unchanged`, `test_injects_nearest_node`, `step_sibling_isolated`, `test_rewriter_input_is_exactly_the_session_reports_no_sibling_filter` |
| reference: Files | Injection is capped at 9500 characters | `test_injection_never_exceeds_cap` |
| reference: Files | Parent keeps its own summary; 8 full entries, then 80 characters, then a count | `test_rewritten_node_keeps_its_own_summary`, `test_subs_rule_8_full_80_chars_plus_n`, `test_bulk_children_bounded` |
| how-it-works: Guards | The rewrite is `claude -p` (haiku default), no tools, no hooks | `test_rewriter_command_is_claude_p_without_tools_or_hooks`, `test_sync_inner_env_in_rewriter_command` |
| reference: Configuration and environment | `~/.ctx/config` (`rewriter` = `claude` only, `model`); an unsupported rewriter is reported with a clear message | `test_config_parsing_comments_quotes_unknown_keys_and_bad_lines`, `test_env_ctx_rewriter_is_gone`, `test_other_rewriter_names_give_a_clear_message`, `test_doctor_reports_an_unsupported_rewriter`, `test_doctor_shows_the_rewriter_and_checks_the_command_exists` |
| how-it-works: Guards | Secrets and instruction-like lines are filtered on a best-effort, pattern basis; invalid output keeps the old file | `test_secret_filtered_in_input_and_output`, `test_instruction_lines_dropped`, `test_invalid_output_keeps_old_file` |
| reference: Configuration and environment | `CTX_OFF=1` disables the hooks | `test_ctx_off_makes_hooks_silent`, `test_ctx_off_values` |
| how-it-works: Guards | `log` and `doctor` output cannot be forged by control characters in logged or hand-written lines | `test_log_strips_control_chars_and_cannot_forge_lines_or_pairs`, `test_log_and_doctor_output_sanitise_old_or_hand_written_lines` |
| how-it-works: Guards | An abandoned sync's cause is logged inside the data frame, filtered and redacted | `test_abandoned_cause_is_inside_the_data_frame_filtered_and_redacted` |
| reference: Files | The log is rotated at 1 MB without losing lines under concurrent writers | `test_log_rotation_with_concurrent_writers_loses_nothing`, `test_log_rotation_sessions_purge_history_cap_and_size` |
| reference: Log and doctor | `doctor --fix` skips folders whose parent is unreachable; moved or deleted nodes are handled | `test_doctor_fix_skips_folders_whose_parent_is_unreachable`, `test_moved_renamed_deleted_nodes_and_doctor_fix` |
| how-it-works: When things break | A first sync of a node without a hash logs "external edit unknown" once | `test_first_sync_of_a_node_without_hash_logs_external_edit_unknown_once` |
| reference: Configuration and environment | A model name starting with a dash is rejected | `test_config_rejects_dash_model` |
| how-it-works: When things break | After a killed, cut, slow or failing sync the old file is kept (no partial write): the group is killed on timeout, abandoned syncs are visible and retried by hand, hand edits during a sync win, hooks always exit 0 | `test_kill_minus_9_of_the_sync_group_during_the_model_call`, `test_cut_between_node_rewrite_and_parent_rebuild`, `test_cut_between_session_end_hook_and_popen`, `test_sleep_and_wsl_stop`, `test_model_call_impossible_four_causes`, `test_abandon_is_visible_and_retry_by_hand`, `test_timeout_kills_the_whole_process_group`, `test_sync_deadline_also_kills_the_group`, `test_external_write_is_the_base_and_conflict_files_are_ignored`, `test_hand_edit_during_a_sync`, `test_missing_ctx_dir_is_recreated_and_hooks_never_crash`, `test_read_only_home_hooks_exit_0`, `test_disk_full_hook_exits_0_and_logs`, `test_slow_hook_gives_up_before_the_cli_delay` |
| Uninstall | The journey ends with a clean uninstall in a temporary HOME | `step_uninstall` |
| Limits | The rewrite needs `claude`; cursor and agy are hook clients only (no rewriter) | `test_no_config_file_is_fine`, `test_install_hooks_cursor_prints_no_rewriter_line` |
| reference: Behaviour details | agy sessions are synced at the next session start | `test_agy_post_tool_use_collects_only_edit_tools_and_answers_empty_object`, `test_catch_up_finds_crashed_session` |
| reference: Behaviour details | 600 s window for a session without end signal | `test_numbers_are_the_constants`, `test_dead_pid_caught_at_once_live_pid_skipped_no_pid_falls_back` |
| Limits | Linux, WSL, macOS only (`fcntl`); opencode unsupported | `test_platform_limit_fcntl`, `test_clients_named_in_readme_are_supported` |
| Measured | The headline numbers (quality, tokens, turns, time, with their 95 % CI) are those of docs/benchmark.md, measured on real folders by `tests/integration/run_benchmark.py`; its statistics and change checks are unit-tested | `test_measured_numbers_match_the_benchmark_doc`, `test_measured_check_catches_drift`, `test_paired_difference_ci_and_cell_counts`, `test_stat_manifest_ignores_context_and_git_and_sees_changes` |
| Measured over long chains | Over 10-session chains on two synthetic projects ctx used fewer tokens but did not improve continuity, with three weaknesses; the sentence is the one of docs/benchmark-longrun.md, measured by `tests/integration/run_longrun.py`, whose metrics are unit-tested | `test_readme_long_run_sentence_matches_the_doc`, `test_progress_counts_the_true_next_item`, `test_no_arm_hint_left`, `test_done_and_pending_claims` |
| License | The project is MIT-licensed (`LICENSE` file) | `test_license_is_mit_with_owner` |
| reference: Nodes | Only files with `ctx: 1` are nodes; any other `CONTEXT.md` (even in ctx's format) is foreign: ignored, listed by `doctor` as `info:` (exit 0), never written by `doctor --fix` or `init` | `test_foreign_context_is_not_a_node_anywhere`, `test_marker_less_file_in_ctx_shape_is_foreign_too` |
| reference: Nodes | Only summary + 4 known sections are injected | `test_injection_allow_list` |
| reference: Configuration and environment | The sync deadline covers two model calls (first + retry) plus a margin | `test_sync_deadline_covers_two_model_calls` |

Claims proven only by the integration runs (a real `claude -p`, not run by the offline suite): the discovery question
and "yes" creating a node (`step_discovery_yes`), the three-level update after real work (`step_work_and_sync`), sibling
isolation with a real model (`step_sibling_isolated`) and the real-CLI end of the journey (`step_uninstall`). Their
offline counterparts above check the same code paths with a fake rewriter, not the model's behaviour.
`tests/integration/run_install.py` (temporary HOME, no model) is run by hand;
the offline suite covers the same install/uninstall logic in-process.

Not covered by a unit test, therefore not claimed: hook behaviour inside each real CLI beyond what
the integration runs measured (see [clients.md](clients.md)).
