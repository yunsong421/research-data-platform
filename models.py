import os
from datetime import datetime
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, LargeBinary, DateTime, Date, ForeignKey, Float
from sqlalchemy.orm import relationship

# Database setup
from config import DATA_DIR

DATABASE_URL = 'sqlite:///' + str(DATA_DIR / 'data_website.db')
engine = create_engine(DATABASE_URL, echo=False)
db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()


# Enable WAL mode and foreign keys
def _enable_pragmas(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

event.listen(engine, 'connect', _enable_pragmas)


class ChartDataset(Base):
    __tablename__ = 'chart_datasets'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(240), nullable=False)
    data_json = Column(Text, nullable=False)
    row_count = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class Questionnaire(Base):
    __tablename__ = 'questionnaires'

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, default='')
    content = Column(Text, default='{}')
    respondent_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    @property
    def tag_items(self):
        from models import db_session, ItemTag, Tag
        its = db_session.query(ItemTag).filter(ItemTag.item_id == self.id, ItemTag.item_type == 'questionnaire').all()
        return [it.tag for it in its if it.tag]

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'content': self.content,
            'respondent_count': self.respondent_count,
            'tags': [t.to_dict() for t in self.tag_items],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class InterviewRecording(Base):
    __tablename__ = 'interview_recordings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    interviewee = Column(String(100), default='')
    interviewer = Column(String(100), default='')
    interview_date = Column(Date, nullable=True)
    notes = Column(Text, default='')
    transcript = Column(Text, default='')
    duration = Column(Integer, default=0)
    file_data = Column(LargeBinary, nullable=True)
    file_name = Column(String(500), default='')
    file_type = Column(String(100), default='')
    file_size = Column(Integer, default=0)
    category = Column(String(20), default='student')
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    @property
    def tag_items(self):
        from models import db_session, ItemTag, Tag
        its = db_session.query(ItemTag).filter(ItemTag.item_id == self.id, ItemTag.item_type == 'interview').all()
        return [it.tag for it in its if it.tag]

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'interviewee': self.interviewee,
            'interviewer': self.interviewer,
            'interview_date': self.interview_date.isoformat() if self.interview_date else None,
            'notes': self.notes,
            'transcript': self.transcript,
            'duration': self.duration,
            'file_name': self.file_name,
            'file_type': self.file_type,
            'file_size': self.file_size,
            'category': self.category or 'student',
            'tags': [t.to_dict() for t in self.tag_items],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Document(Base):
    __tablename__ = 'documents'

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, default='')
    file_data = Column(LargeBinary, nullable=True)
    file_name = Column(String(500), default='')
    file_type = Column(String(100), default='')
    file_size = Column(Integer, default=0)
    thumbnail = Column(LargeBinary, nullable=True)
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    category = Column(String(20), default='student')
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    @property
    def tag_items(self):
        from models import db_session, ItemTag, Tag
        its = db_session.query(ItemTag).filter(ItemTag.item_id == self.id, ItemTag.item_type == 'document').all()
        return [it.tag for it in its if it.tag]

    def to_dict(self, include_file=False):
        d = {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'file_name': self.file_name,
            'file_type': self.file_type,
            'file_size': self.file_size,
            'category': self.category or 'student',
            'width': self.width,
            'height': self.height,
            'tags': [t.to_dict() for t in self.tag_items],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_file and self.thumbnail:
            from base64 import b64encode
            d['thumbnail_b64'] = b64encode(self.thumbnail).decode('utf-8')
        return d


class Tag(Base):
    __tablename__ = 'tags'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False, unique=True)
    color = Column(String(7), default='#6366f1')
    created_at = Column(DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'color': self.color,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)

    def set_password(self, raw_password):
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        from werkzeug.security import check_password_hash
        try:
            return check_password_hash(self.password_hash, raw_password)
        except Exception:
            return False

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'is_admin': bool(self.is_admin),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class ItemTag(Base):
    __tablename__ = 'item_tags'

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, nullable=False)
    item_type = Column(String(30), nullable=False)
    tag_id = Column(Integer, ForeignKey('tags.id', ondelete='CASCADE'), nullable=False)

    tag = relationship('Tag', lazy='joined')


def init_db():
    import json
    Base.metadata.create_all(bind=engine)

    conn = engine.raw_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("CREATE VIRTUAL TABLE IF NOT EXISTS questionnaires_fts USING fts5(title, description, content, content='questionnaires', content_rowid='id')")
        cursor.execute("CREATE VIRTUAL TABLE IF NOT EXISTS interviews_fts USING fts5(title, notes, transcript, interviewee, interviewer, content='interview_recordings', content_rowid='id')")
        cursor.execute("CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(title, description, content='documents', content_rowid='id')")

        for table, fts_table, cols in [
            ('questionnaires', 'questionnaires_fts', ['title', 'description', 'content']),
            ('interview_recordings', 'interviews_fts', ['title', 'notes', 'transcript', 'interviewee', 'interviewer']),
            ('documents', 'documents_fts', ['title', 'description']),
        ]:
            cursor.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_ai AFTER INSERT ON {table} BEGIN
                    INSERT INTO {fts_table}(rowid, {','.join(cols)})
                    VALUES (new.id, {','.join(f'new.{c}' for c in cols)});
                END
            """)
            cursor.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_ad AFTER DELETE ON {table} BEGIN
                    INSERT INTO {fts_table}({fts_table}, rowid, {','.join(cols)})
                    VALUES ('delete', old.id, {','.join(f'old.{c}' for c in cols)});
                END
            """)
            cursor.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_au AFTER UPDATE ON {table} BEGIN
                    INSERT INTO {fts_table}({fts_table}, rowid, {','.join(cols)})
                    VALUES ('delete', old.id, {','.join(f'old.{c}' for c in cols)});
                    INSERT INTO {fts_table}(rowid, {','.join(cols)})
                    VALUES (new.id, {','.join(f'new.{c}' for c in cols)});
                END
            """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"FTS setup warning: {e}")
    finally:
        cursor.close()
        conn.close()


def init_app():
    init_db()


def get_db():
    return db_session
