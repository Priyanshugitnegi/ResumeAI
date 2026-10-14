
import os
import json
import re

from dotenv import load_dotenv
from groq import Groq
from schemas import CandidateAnalysis

load_dotenv()

api_key = os.getenv("GROQ_API_KEY")
client = Groq(api_key=api_key) if api_key else None


def clean_list(value):
    """Return unique, non-empty strings."""
    if isinstance(value, str):
        value = [value]

    if not isinstance(value, list):
        return []

    cleaned = []
    seen = set()

    for item in value:
        if isinstance(item, str) and item.strip():
            item = item.strip()
            key = item.casefold()

            if key not in seen:
                cleaned.append(item)
                seen.add(key)

    return cleaned


def normalize_skill(skill):
    """Normalize text for comparison."""
    return re.sub(r"\s+", " ", skill.casefold()).strip()


def extract_json(content):
    """Parse a JSON response, including one wrapped in code fences."""
    if not content:
        raise ValueError("Groq returned an empty response.")

    content = content.strip()

    content = re.sub(
        r"\A```(?:json)?\s*|\s*```\Z",
        "",
        content,
        flags=re.IGNORECASE,
    ).strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")

        if start == -1 or end <= start:
            raise ValueError(
                "Groq did not return complete JSON. Please retry."
            )

        data = json.loads(content[start:end + 1])

    if not isinstance(data, dict):
        raise ValueError("Groq response must be a JSON object.")

    return data


def analyze_resume(resume_text, job_description):
    """
    Analyze a resume against a job description.

    Groq extracts skills and identifies relevant requirements.
    Python calculates the final match percentage.
    """

    if client is None:
        raise ValueError(
            "GROQ_API_KEY is missing. Check your .env file."
        )

    if not resume_text or not resume_text.strip():
        raise ValueError("No readable text found in the resume.")

    if not job_description or not job_description.strip():
        raise ValueError("Please enter a job description.")

    prompt = f"""
You are a resume screening assistant.

Analyze the resume against the job description.
The job may belong to any profession or industry.

JOB DESCRIPTION:
{job_description[:10000]}

RESUME:
{resume_text[:10000]}

Return one valid JSON object with exactly these fields:
- candidate_name: string
- skills: array of strings
- required_skills: array of strings
- matched_skills: array of strings
- strengths: array of strings
- missing_skills: array of strings
- summary: string

Rules:
1. Use only evidence from the resume.
2. Extract relevant skills from any profession.
3. List distinct skills explicitly required by the JD.
4. Exclude preferred skills unless stated as required.
5. Match equivalent terminology and common abbreviations.
6. A matched skill must have evidence in the resume.
7. Do not invent skills, experience, or qualifications.
8. Do not include personal or protected traits in the evaluation.
9. Do not calculate or return a match score.
10. Keep the summary concise.
11. Return JSON only, without Markdown.
"""

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Analyze resumes accurately and fairly. "
                        "Use evidence from the supplied resume. "
                        "Return valid JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_completion_tokens=2048,
        )

        result = extract_json(
            response.choices[0].message.content
        )

    except Exception as exc:
        raise RuntimeError(
            "Resume analysis failed. Check the Groq API error, "
            "model availability, API limits, and try again."
        ) from exc

    # Clean and validate the extracted fields.
    candidate_name = result.get("candidate_name", "Unknown")

    if not isinstance(candidate_name, str):
        candidate_name = "Unknown"

    result["candidate_name"] = (
        candidate_name.strip() or "Unknown"
    )

    for field in (
        "skills",
        "required_skills",
        "matched_skills",
        "strengths",
        "missing_skills",
    ):
        result[field] = clean_list(result.get(field, []))

    summary = result.get("summary", "")
    result["summary"] = (
        summary.strip() if isinstance(summary, str) else ""
    )

    # Remove duplicate requirements while preserving their order.
    required = result["required_skills"]

    required_lookup = {
        normalize_skill(skill): skill
        for skill in required
    }

    # Only accept matches that correspond to a required skill.
    matched = []
    matched_keys = set()

    for skill in result["matched_skills"]:
        key = normalize_skill(skill)

        if key in required_lookup and key not in matched_keys:
            matched.append(required_lookup[key])
            matched_keys.add(key)

    # Calculate missing skills from the final matched list.
    missing = [
        skill
        for skill in required
        if normalize_skill(skill) not in matched_keys
    ]

    # Python calculates the score, never the LLM.
    if required:
        score = round(
            len(matched) / len(required) * 100,
            2,
        )
    else:
        score = 0.0

    result["required_skills"] = required
    result["matched_skills"] = matched
    result["missing_skills"] = missing
    result["match_score"] = score

    # Return the validated Pydantic model used by the app.
    return CandidateAnalysis.model_validate(result)
