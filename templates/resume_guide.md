# Resume HTML Generation Guide

You will generate HTML for the `<body>` of a resume PDF. The wrapper HTML/CSS is already applied - you only output the body content.

## CSS Classes Available

These classes are pre-defined and styled. Use them exactly as shown:

### Header
```html
<header class="header">
    <h1 class="name">FULL NAME</h1>
    <div class="contact-line">
        <a href="mailto:email@example.com">email@example.com</a>
        <span class="sep">|</span>
        <span>555-123-4567</span>
        <span class="sep">|</span>
        <a href="https://linkedin.com/in/username">linkedin.com/in/username</a>
        <span class="sep">|</span>
        <a href="https://github.com/username">github.com/username</a>
    </div>
</header>
```

The contact line automatically wraps to a second line if there are many items — no extra CSS needed.

### Sections
```html
<section class="section">
    <h2 class="section-title">SECTION NAME</h2>
    <div class="section-content">
        <!-- content here -->
    </div>
</section>
```

### Summary
```html
<div class="summary">
    Summary text goes here. Keep it concise and impactful.
</div>
```

### Experience/Education Entry

**IMPORTANT: Company and title go on ONE line, date on the right:**
```html
<div class="entry">
    <div class="entry-header">
        <div class="entry-main">
            <span class="company">Company Name</span> - <span class="title">Job Title</span>
        </div>
        <div class="entry-date">Jan 2020 - Present</div>
    </div>
    <ul class="bullets">
        <li>Achievement or responsibility with quantified impact</li>
        <li>Another bullet point</li>
    </ul>
</div>
```

For education:
```html
<div class="entry">
    <div class="entry-header">
        <div class="entry-main">
            <span class="institution">University Name</span> - <span class="degree">BS Computer Science</span>
        </div>
        <div class="entry-date">2016 - 2020</div>
    </div>
    <ul class="bullets">
        <li>GPA: 3.8/4.0, Dean's List</li>
    </ul>
</div>
```

### Skills

Use `<strong>` for category labels (NOT markdown `**bold**`):
```html
<div class="skills-list">
    <strong>Languages:</strong> Python, JavaScript, TypeScript, Go<br>
    <strong>Frameworks:</strong> React, Node.js, FastAPI, Django<br>
    <strong>Tools:</strong> PostgreSQL, AWS, Docker, Kubernetes
</div>
```

### Projects
```html
<div class="project">
    <span class="project-name"><a href="https://github.com/user/project">Project Name</a></span> - Brief description
    <ul class="bullets">
        <li>Technical detail or achievement</li>
    </ul>
</div>
```

### Simple Lists (Certifications, Publications)
```html
<ul class="simple-list">
    <li>AWS Certified Solutions Architect, 2023</li>
    <li>Doe J. et al., "Title of Paper", Nature 2022 (DOI: 10.1000/xyz123)</li>
</ul>
```

For publications: include DOI in parentheses at the end if available.

## Visual Criteria

- **Font size:** 11pt-14pt range (body 11pt, name 14pt) - readable at arm's length
- **Margins:** ~0.5in all sides (already set in wrapper)
- **Length:** Single page preferred, 2 pages max
- **Section headers:** Clear uppercase with underline
- **Spacing:** Consistent gaps between entries

## Layout Guidelines

1. **One page preferred** - Be concise. Remove less relevant content if needed.
2. **Fill the page** - Aim to use the full page without overflow.
3. **Section order** - Typical order: Summary, Experience, Education, Skills, Projects, Certifications, Publications. Adjust based on relevance to job.
4. **Bullet points** - Max 3-5 per job. Focus on impact, not tasks. Include metrics.
5. **Dates** - Use consistent format (e.g., "Jan 2020" or "2020").
6. **Entry layout** - Company/title on ONE line with date right-aligned (never stacked).

## Professional Standards

### Formatting
- Consistent date format throughout (all "Jan 2020" or all "01/2020", not mixed)
- Parallel structure in bullets (all start with past-tense verbs, or all present-tense)
- No orphan lines (single line alone at top/bottom of page)

### Language
- No first person ("I", "my", "me")
- Active voice, strong verbs ("Led", "Built", "Reduced" not "Was responsible for")
- No fluff words ("various", "helped with", "assisted in")
- No slang or casual tone

### Content
- Each bullet has: Action + Context + Result (ideally quantified)
- No generic statements ("team player", "hard worker")
- Specific technologies named, not "various tools"

### Visual Balance
- Balanced whitespace - no section looks cramped or empty
- Alignment consistent (all dates right-aligned, all bullets same indent)
- No walls of text - max 3 lines per bullet

## Content Rules

- Show concrete results with metrics when available
- Feature keywords matching job requirements
- Prioritize experiences most relevant to the role
- Preserve original writing style where possible
- Include all URLs from original resume

## What NOT to Do

- Never add `<script>` tags
- Never add content not in the original resume
- Never fabricate metrics, titles, or achievements
- Never use `<html>`, `<head>`, or `<body>` tags - only body content
- **Never use markdown syntax** (`**bold**`, `*italic*`) - use HTML (`<strong>`, `<em>`)
- **Never put company and title on separate lines** - keep on one line with dash separator
- **Never make text smaller than 11pt or larger than 14pt** - maintain readability

## Custom Styling

You CAN add a `<style>` tag at the beginning of your output for custom CSS if needed.
Prefer using the provided classes, but custom styles are allowed for layout adjustments.
