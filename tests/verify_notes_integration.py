import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from hr_breaker.agents.cover_letter_generator import generate_cover_letter
from hr_breaker.models import JobPosting, ResumeSource


async def main():
    print("Testing User Notes Integration...")

    resume_content = """
    John Doe
    Software Engineer.
    """
    
    source = ResumeSource(content=resume_content, first_name="John", last_name="Doe")

    job = JobPosting(
        title="Python Developer",
        company="TestCorp",
        description="Looking for Python dev.",
        requirements=["Python"],
        keywords=["Python"],
    )
    
    notes = "mention my massive love for pizza in the intro"

    print(f"Generating cover letter with notes: '{notes}'")
    try:
        cover_letter = await generate_cover_letter(source, job, user_notes=notes)
        print("\n--- Generated Cover Letter ---")
        print(cover_letter.markdown)
        print("\n------------------------------")
        
        if "pizza" in cover_letter.markdown.lower():
            print("SUCCESS: Notes were incorporated (found 'pizza').")
        else:
            print("WARNING: Notes might not have been incorporated (did not find 'pizza').")
            
    except Exception as e:
        print(f"ERROR: Failed to generate: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
