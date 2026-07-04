from typing import TypedDict


class JobRecordBase(TypedDict):
    score: int
    source: str
    company: str
    title: str
    location: str
    url: str
    tags: str
    skills: str
    posted: str


class JobRecord(JobRecordBase, total=False):
    age_days: int
