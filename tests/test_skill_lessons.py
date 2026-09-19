from sqlalchemy import select

from hagent import db as hagent_db
from hagent import engine as agent_engine
from hagent.adapters.base import RuntimeResult
from hagent.models import Agent, Project, Issue, Runtime, RuntimeType, Skill, SkillLesson, Workspace


def _seed(cli_db):
    with hagent_db.SessionLocal() as session:
        workspace = Workspace(name="lesson-ws")
        session.add(workspace)
        session.flush()
        skill = Skill(workspace_id=workspace.id, name="Code Review", content="Review carefully")
        project = Project(workspace_id=workspace.id, name="project")
        session.add_all([skill, project])
        session.flush()
        issue = Issue(project_id=project.id, title="Review change")
        session.add(issue)
        session.commit()
        return workspace.id, skill.id, issue.id


def test_lesson_is_redacted_deduplicated_counted_and_mirrored(cli_db, tmp_path, monkeypatch):
    workspace_id, skill_id, issue_id = _seed(cli_db)
    monkeypatch.setattr(agent_engine, "SKILL_NOTES_DIR", tmp_path / "skill-notes")
    raw = "Review missed validation; API_KEY=supersecretvalue123"

    assert agent_engine._learn_from_mistake([skill_id], raw, issue_id=issue_id,
                                             source="verifier_reject") == 1
    assert agent_engine._learn_from_mistake([skill_id], raw, issue_id=issue_id,
                                             source="verifier_reject") == 0

    with hagent_db.SessionLocal() as session:
        skill = session.get(Skill, skill_id)
        lessons = session.scalars(select(SkillLesson).where(SkillLesson.skill_id == skill_id)).all()
        assert skill.improvement_count == 1
        assert len(lessons) == 1
        assert "supersecretvalue123" not in lessons[0].text
        assert "[REDACTED]" in lessons[0].text
        path = agent_engine._skill_notes_path(skill)

    assert workspace_id in str(path)
    assert path.exists()
    assert "supersecretvalue123" not in path.read_text(encoding="utf-8")


def test_infrastructure_failures_are_not_skill_lessons(cli_db, tmp_path, monkeypatch):
    _workspace_id, skill_id, issue_id = _seed(cli_db)
    monkeypatch.setattr(agent_engine, "SKILL_NOTES_DIR", tmp_path / "skill-notes")

    assert agent_engine._learn_from_mistake(
        [skill_id], "Provider rate limit quota exceeded", issue_id=issue_id, source="run_failure"
    ) == 0

    with hagent_db.SessionLocal() as session:
        assert session.scalar(select(SkillLesson).where(SkillLesson.skill_id == skill_id)) is None
        assert session.get(Skill, skill_id).improvement_count == 0


def test_skill_lessons_follow_workspace_scope(cli_db):
    with hagent_db.SessionLocal() as session:
        ids = {}
        for name in ("a", "b"):
            workspace = Workspace(name=name)
            session.add(workspace)
            session.flush()
            skill = Skill(workspace_id=workspace.id, name=f"skill-{name}")
            session.add(skill)
            session.flush()
            session.add(SkillLesson(skill_id=skill.id, source="verifier_reject", text=name))
            ids[name] = workspace.id
        session.commit()

    with hagent_db.SessionLocal() as session:
        session.info["workspace_id"] = ids["a"]
        assert [lesson.text for lesson in session.scalars(select(SkillLesson)).all()] == ["a"]


def test_agent_can_recall_recent_lessons_on_demand(session, mocker):
    workspace = Workspace(name="recall-ws")
    session.add(workspace)
    session.flush()
    runtime = Runtime(workspace_id=workspace.id, name="local", type=RuntimeType.OLLAMA,
                      model="test", config_json="{}")
    skill = Skill(workspace_id=workspace.id, name="Review", content="Review carefully",
                  improvement_count=1)
    session.add_all([runtime, skill])
    session.flush()
    agent = Agent(workspace_id=workspace.id, runtime_id=runtime.id, name="reviewer",
                  instructions="Be accurate")
    agent.skills = [skill]
    session.add(agent)
    session.flush()
    session.add(SkillLesson(skill_id=skill.id, source="verifier_reject",
                            text="Check boundary validation before approval."))
    session.commit()

    def use_recall(_runtime, *, context, tools, tool_executor, **_kwargs):
        names = [tool["function"]["name"] for tool in tools if "function" in tool]
        assert "recall_lessons" in names
        assert "1 lesson(s) learned" in context
        recalled = tool_executor("recall_lessons", {"skill_name": "Review"})
        return RuntimeResult(output=recalled)

    mocker.patch("hagent.adapters.ollama.OllamaRuntime.run", autospec=True, side_effect=use_recall)

    result = agent_engine.execute_agent(agent, "Review this change")

    assert "Check boundary validation before approval." in result.output
