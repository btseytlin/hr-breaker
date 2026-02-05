import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from hr_breaker.agents.cover_letter_generator import generate_cover_letter
from hr_breaker.models import JobPosting, ResumeSource


async def main():
    print("Testing Cover Letter Generator...")

    resume_content = """
    John Doe
    Software Engineer with 5 years of experience in Python and Cloud.
    Built scalable APIs using FastAPI and deployed on AWS.
    """
    
    source = ResumeSource(content=resume_content, first_name="John", last_name="Doe")

    job = JobPosting(
        title="Senior Python Developer",
        company="TechCorp",
        description="We are looking for a Python expert with AWS experience to build our next gen platform.",
        requirements=["Python", "AWS", "FastAPI"],
        keywords=["Python", "Cloud", "API"],
    )

    print("Generating cover letter...")
    try:
        cover_letter = await generate_cover_letter(source, job)
        print("\n--- Generated Cover Letter ---")
        print(f"Company: {cover_letter.job_company}")
        print(f"Title: {cover_letter.job_title}")
        print("\nContent:")
        print(cover_letter.markdown)
        print("\n------------------------------")
        print("SUCCESS: Cover letter generated successfully.")
    except Exception as e:
        print(f"ERROR: Failed to generate cover letter: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
