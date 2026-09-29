"""Choosing resume content for a job posting from an authored library, with no generated prose.

reading/     fetch a posting, read its facts, and read it with saved model calls: its requirements, their capability
             mapping and, in hybrid mode, the project choice
library/     the candidate's projects, bullets, skills, education and capability vocabulary
selection/   choose the page's bullets, skill rows and courses for one posting; in hybrid mode, from call 3's projects
             where the engine's own match is low
rendering/   the page as a document, written into the Word template, and as the judges read it
"""
