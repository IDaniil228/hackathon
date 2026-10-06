-- =============================================================================
--  70. Индексы
-- =============================================================================
--  PostgreSQL сам создаёт индексы под PRIMARY KEY и UNIQUE, но не под внешние
--  ключи. Здесь покрыты FK, по которым реально идут джойны и фильтры, плюс
--  индексы под конкретные сценарии обоих кабинетов. Партиальные индексы
--  предпочтены полным там, где запрос всегда идёт с тем же условием.
-- =============================================================================

-- --- core --------------------------------------------------------------------
CREATE INDEX users_status_idx             ON core.users (status);
CREATE INDEX user_roles_role_idx          ON core.user_roles (role);
CREATE INDEX email_tokens_user_idx        ON core.email_tokens (user_id);
-- выборка живых токенов при подтверждении адреса
CREATE INDEX email_tokens_active_idx      ON core.email_tokens (user_id, purpose) WHERE used_at IS NULL;
CREATE INDEX refresh_sessions_user_idx    ON core.refresh_sessions (user_id) WHERE revoked_at IS NULL;
CREATE INDEX user_consents_user_idx       ON core.user_consents (user_id);

-- --- ref ---------------------------------------------------------------------
CREATE INDEX specializations_industry_idx ON ref.specializations (industry_id);
CREATE INDEX specializations_parent_idx   ON ref.specializations (parent_id);
CREATE INDEX grades_industry_idx          ON ref.grades (industry_id);
CREATE INDEX categories_grade_idx         ON ref.categories (grade_id);
CREATE INDEX skills_area_idx              ON ref.skills (area_id);
CREATE INDEX skills_kind_idx              ON ref.skills (kind);
-- автодополнение по названию навыка
CREATE INDEX skills_name_trgm_idx         ON ref.skills USING gin (name gin_trgm_ops);
CREATE INDEX skill_aliases_skill_idx      ON ref.skill_aliases (skill_id);
CREATE INDEX specialization_skills_skill_idx ON ref.specialization_skills (skill_id);
CREATE INDEX attribute_defs_industry_idx  ON ref.attribute_defs (industry_id);
CREATE INDEX attribute_options_attr_idx   ON ref.attribute_options (attribute_id);

-- --- profile -----------------------------------------------------------------
-- главный путь банка кандидатов: категория + готовность к предложениям
CREATE INDEX candidates_category_idx      ON profile.candidates (current_category_id) WHERE is_open_to_offers;
-- ранжирование внутри категории по силе подтверждённого профиля
CREATE INDEX candidates_category_strength_idx
    ON profile.candidates (current_category_id, profile_strength DESC);
CREATE INDEX candidates_city_idx          ON profile.candidates (city);
CREATE INDEX candidates_search_idx        ON profile.candidates USING gin (search_vector);

CREATE INDEX candidate_links_candidate_idx ON profile.candidate_links (candidate_id);
-- обратный проход: «кто владеет навыком X на уровне не ниже Y»
CREATE INDEX candidate_skills_skill_idx   ON profile.candidate_skills (skill_id, confirmed_level DESC NULLS LAST);
CREATE INDEX candidate_team_roles_role_idx ON profile.candidate_team_roles (team_role_id);
CREATE INDEX cav_candidate_idx            ON profile.candidate_attribute_values (candidate_id);
CREATE INDEX cav_attribute_idx            ON profile.candidate_attribute_values (attribute_id);

CREATE INDEX resume_documents_candidate_idx ON profile.resume_documents (candidate_id, uploaded_at DESC);
CREATE INDEX resume_documents_pending_idx   ON profile.resume_documents (parse_status) WHERE parse_status IN ('pending', 'processing');
CREATE INDEX resume_parse_results_doc_idx   ON profile.resume_parse_results (document_id);

CREATE INDEX fsp_profiles_status_idx      ON profile.fsp_profiles (verification_status);
CREATE INDEX fsp_achievements_candidate_idx ON profile.fsp_achievements (candidate_id);
CREATE INDEX fsp_achievements_event_idx   ON profile.fsp_achievements (event_id);
CREATE INDEX fsp_events_level_idx         ON profile.fsp_events (level, starts_on DESC);

CREATE INDEX github_repos_candidate_idx   ON profile.github_repos (candidate_id);
CREATE INDEX github_signals_skill_idx     ON profile.github_signals (skill_id);

-- --- assess ------------------------------------------------------------------
CREATE INDEX survey_questions_survey_idx  ON assess.survey_questions (survey_id);
CREATE INDEX survey_options_question_idx  ON assess.survey_options (question_id);
CREATE INDEX survey_sessions_candidate_idx ON assess.survey_sessions (candidate_id, started_at DESC);
CREATE INDEX survey_answers_session_idx   ON assess.survey_answers (session_id);

-- подбор шаблонов под генерацию: специализация + грейд + активность
CREATE INDEX task_templates_target_idx    ON assess.task_templates (specialization_id, grade_id) WHERE is_active;
CREATE INDEX task_templates_skill_idx     ON assess.task_templates (skill_id);
CREATE INDEX tasks_template_idx           ON assess.tasks (template_id);
CREATE INDEX tasks_candidate_idx          ON assess.tasks (candidate_id, created_at DESC);

CREATE INDEX test_sessions_candidate_idx  ON assess.test_sessions (candidate_id, created_at DESC);
-- фоновая задача «просрочить зависшие сессии»
CREATE INDEX test_sessions_open_idx       ON assess.test_sessions (deadline_at) WHERE status IN ('created', 'in_progress');
CREATE INDEX task_answers_session_idx     ON assess.task_answers (session_id);
CREATE INDEX task_answers_unchecked_idx   ON assess.task_answers (submitted_at) WHERE checked_at IS NULL;

CREATE INDEX grade_assignments_candidate_idx ON assess.grade_assignments (candidate_id, assigned_at DESC);
CREATE INDEX grade_assignments_category_idx  ON assess.grade_assignments (category_id) WHERE is_current;
CREATE INDEX regular_assignments_candidate_idx ON assess.regular_assignments (candidate_id, issued_at DESC);
CREATE INDEX regular_assignments_open_idx    ON assess.regular_assignments (due_at) WHERE status IN ('issued', 'viewed');
CREATE INDEX regular_assignments_company_idx ON assess.regular_assignments (company_id);

-- --- hire --------------------------------------------------------------------
CREATE INDEX employers_company_idx        ON hire.employers (company_id);
CREATE INDEX vacancies_company_idx        ON hire.vacancies (company_id, created_at DESC);
-- витрина опубликованных вакансий по категориям
CREATE INDEX vacancies_published_idx      ON hire.vacancies (category_id, published_at DESC) WHERE status = 'published';
CREATE INDEX vacancies_search_idx         ON hire.vacancies USING gin (search_vector);
CREATE INDEX vacancy_skills_skill_idx     ON hire.vacancy_skills (skill_id);

CREATE INDEX employer_needs_company_idx   ON hire.employer_needs (company_id, created_at DESC) WHERE is_active;
CREATE INDEX need_skills_skill_idx        ON hire.need_skills (skill_id);

-- лента входящих приглашений в кабинете кандидата
CREATE INDEX invitations_candidate_idx    ON hire.invitations (candidate_id, status, sent_at DESC);
-- отслеживание статусов в кабинете работодателя
CREATE INDEX invitations_company_idx      ON hire.invitations (company_id, status, sent_at DESC);
CREATE INDEX invitations_vacancy_idx      ON hire.invitations (vacancy_id);
CREATE INDEX invitations_need_idx         ON hire.invitations (need_id);
CREATE INDEX invitation_events_invitation_idx ON hire.invitation_events (invitation_id, created_at);

CREATE INDEX applications_candidate_idx   ON hire.applications (candidate_id, created_at DESC);
CREATE INDEX applications_vacancy_idx     ON hire.applications (vacancy_id, status);
CREATE INDEX application_events_application_idx ON hire.application_events (application_id, created_at);

CREATE INDEX contact_disclosures_company_idx ON hire.contact_disclosures (company_id);

-- --- match -------------------------------------------------------------------
-- HNSW по косинусу: быстрый ANN-поиск ближайших резюме к описанию потребности.
-- m/ef_construction по умолчанию (16/64) — компромисс для масштаба MVP.
CREATE INDEX candidate_embeddings_hnsw_idx ON match.candidate_embeddings
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX vacancy_embeddings_hnsw_idx   ON match.vacancy_embeddings
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX need_embeddings_hnsw_idx      ON match.need_embeddings
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX search_runs_need_idx          ON match.search_runs (need_id, created_at DESC);
CREATE INDEX search_runs_parent_idx        ON match.search_runs (parent_run_id);
-- «в какие подборки попадал этот кандидат»
CREATE INDEX search_results_candidate_idx  ON match.search_results (candidate_id);
CREATE INDEX search_results_top_idx        ON match.search_results (run_id, final_score DESC) WHERE is_shown;

CREATE INDEX chat_threads_need_idx         ON match.chat_threads (need_id, updated_at DESC);
CREATE INDEX chat_messages_thread_idx      ON match.chat_messages (thread_id, created_at);
