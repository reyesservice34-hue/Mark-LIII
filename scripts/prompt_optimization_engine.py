#!/usr/bin/env python3
"""
Prompt Optimization Engine
Transforms natural language user input into perfect, structured prompts
"""

import os
import json
import logging
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path
import requests
from enum import Enum

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TaskCategory(Enum):
    """Task categories for prompt optimization."""
    PRICING = "pricing"
    PLANNING = "planning"
    RESEARCH = "research"
    TECHNICAL = "technical"
    ADVISORY = "advisory"
    COMMUNICATION = "communication"
    ANALYSIS = "analysis"
    CREATIVE = "creative"
    OPTIMIZATION = "optimization"
    OTHER = "other"


class PromptOptimizer:
    """Optimizes natural language input into structured prompts."""

    CATEGORY_KEYWORDS = {
        TaskCategory.PRICING: [
            "preis", "kosten", "budget", "preisplan", "angebot", "tarif",
            "gebühr", "berechnung", "kalkulieren", "kostenlos"
        ],
        TaskCategory.PLANNING: [
            "plan", "termin", "zeitplan", "deadline", "schedule", "ablauf",
            "schritte", "roadmap", "agenda", "phasen"
        ],
        TaskCategory.RESEARCH: [
            "recherche", "suche", "finde", "untersuchung", "analyse", "forschung",
            "informationen", "daten", "overview", "zusammenfassung"
        ],
        TaskCategory.TECHNICAL: [
            "code", "script", "automation", "implementation", "entwicklung",
            "programm", "software", "technisch", "system", "api"
        ],
        TaskCategory.ADVISORY: [
            "rat", "vorschlag", "empfehlung", "strategie", "beratung", "meinung",
            "feedback", "insight", "perspektive", "lösungsvorschlag"
        ],
        TaskCategory.COMMUNICATION: [
            "kunde", "mitteilen", "nachricht", "kontakt", "email", "benachrichtigung",
            "kontaktiere", "schreib", "kommuniziere", "anschreiben"
        ],
        TaskCategory.ANALYSIS: [
            "analysiere", "untersuche", "evaluiere", "vergleiche", "bewerte",
            "break down", "dissect", "evaluate", "assessment", "audit"
        ],
        TaskCategory.CREATIVE: [
            "erstelle", "entwerfe", "designiere", "brainstorm", "ideate",
            "schöpferisch", "innovativ", "neu", "original", "kampagne"
        ],
        TaskCategory.OPTIMIZATION: [
            "optimiere", "verbesser", "effizient", "schneller", "besser",
            "maximiere", "minimiere", "performance", "tuning", "refactor"
        ]
    }

    def __init__(self):
        self.history_file = Path("optimized_prompts_history.json")
        self._load_history()

    def _load_history(self):
        """Load optimization history."""
        if self.history_file.exists():
            with open(self.history_file) as f:
                self.history = json.load(f)
        else:
            self.history = {"optimizations": []}

    def _save_history(self):
        """Save optimization history."""
        with open(self.history_file, "w") as f:
            json.dump(self.history, f, indent=2)

    def categorize_input(self, user_input: str) -> TaskCategory:
        """Categorize user input based on keywords."""
        input_lower = user_input.lower()

        # Count keyword matches per category
        scores = {}
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            score = sum(1 for keyword in keywords if keyword in input_lower)
            if score > 0:
                scores[category] = score

        # Return category with highest score, or OTHER
        if scores:
            return max(scores, key=scores.get)
        return TaskCategory.OTHER

    def detect_complexity(self, user_input: str) -> str:
        """Detect task complexity (low, medium, high)."""
        word_count = len(user_input.split())
        has_requirements = any(word in user_input.lower() for word in [
            "muss", "sollte", "erforderlich", "notwendig", "constraints",
            "bedingung", "anforderung", "spezifikation"
        ])
        has_multiple_steps = any(word in user_input.lower() for word in [
            "dann", "danach", "außerdem", "und", "sowie", "mehrere", "schritte"
        ])

        if word_count > 100 or (has_requirements and has_multiple_steps):
            return "high"
        elif word_count > 50 or has_requirements:
            return "medium"
        else:
            return "low"

    def extract_context(self, user_input: str, category: TaskCategory) -> Dict:
        """Extract context from user input."""
        return {
            "original_input": user_input,
            "category": category.value,
            "complexity": self.detect_complexity(user_input),
            "word_count": len(user_input.split()),
            "timestamp": datetime.now().isoformat(),
            "is_german": any(word in user_input.lower() for word in [
                "erstelle", "plane", "recherchiere", "beratung", "optimiere"
            ])
        }

    def create_optimized_prompt(
        self,
        user_input: str,
        category: TaskCategory,
        context: Dict
    ) -> str:
        """Create optimized, structured prompt from user input."""

        # Template based on category
        templates = {
            TaskCategory.PRICING: self._template_pricing,
            TaskCategory.PLANNING: self._template_planning,
            TaskCategory.RESEARCH: self._template_research,
            TaskCategory.TECHNICAL: self._template_technical,
            TaskCategory.ADVISORY: self._template_advisory,
            TaskCategory.COMMUNICATION: self._template_communication,
            TaskCategory.ANALYSIS: self._template_analysis,
            TaskCategory.CREATIVE: self._template_creative,
            TaskCategory.OPTIMIZATION: self._template_optimization,
            TaskCategory.OTHER: self._template_default,
        }

        template_func = templates.get(category, self._template_default)
        return template_func(user_input, context)

    def _template_pricing(self, user_input: str, context: Dict) -> str:
        """Pricing/Quote prompt template."""
        return f"""Task: Create pricing plan or quotation
User Request: {user_input}

Context: {context['complexity'].upper()} complexity
Language: {'German' if context['is_german'] else 'English'}

Requirements:
1. Analyze the user's pricing requirements
2. Break down costs by component
3. Provide clear, professional pricing options
4. Include value justification
5. Consider market rates and competitiveness

Output Format:
- Overview (2-3 sentences)
- Pricing Options (with breakdown)
- What's Included / Not Included
- Next Steps

Tone: Professional, clear, persuasive
Detail Level: {context['complexity'].upper()}"""

    def _template_planning(self, user_input: str, context: Dict) -> str:
        """Planning/Timeline prompt template."""
        return f"""Task: Create project plan or timeline
User Request: {user_input}

Context: {context['complexity'].upper()} complexity
Language: {'German' if context['is_german'] else 'English'}

Requirements:
1. Create a clear, step-by-step plan
2. Define milestones and deadlines
3. Identify dependencies
4. List required resources
5. Include contingency planning

Output Format:
- Executive Summary
- Phase Breakdown
  - Phase 1: [Name] (Timeline)
  - Phase 2: [Name] (Timeline)
  - etc.
- Critical Path
- Risk Mitigation
- Success Metrics

Tone: Structured, actionable, realistic
Detail Level: {context['complexity'].upper()}"""

    def _template_research(self, user_input: str, context: Dict) -> str:
        """Research/Investigation prompt template."""
        return f"""Task: Conduct research and provide findings
User Request: {user_input}

Context: {context['complexity'].upper()} complexity
Language: {'German' if context['is_german'] else 'English'}

Requirements:
1. Gather comprehensive information
2. Verify sources and credibility
3. Identify patterns and insights
4. Present findings objectively
5. Highlight key takeaways

Output Format:
- Executive Summary
- Detailed Findings
  - Finding 1: [Title] - [Details]
  - Finding 2: [Title] - [Details]
  - etc.
- Key Insights
- Recommendations
- Sources/References

Tone: Analytical, informative, unbiased
Detail Level: {context['complexity'].upper()}"""

    def _template_technical(self, user_input: str, context: Dict) -> str:
        """Technical/Code prompt template."""
        return f"""Task: Create technical solution or code
User Request: {user_input}

Context: {context['complexity'].upper()} complexity
Language: {'German' if context['is_german'] else 'English'}

Requirements:
1. Write clean, well-structured code
2. Include comments for clarity
3. Follow best practices
4. Provide error handling
5. Include usage examples

Output Format:
- Code/Solution
- Explanation
  - What it does
  - How it works
  - Key components
- Usage Examples
- Error Handling
- Performance Notes

Tone: Technical, precise, educational
Detail Level: {context['complexity'].upper()}
Code Quality: Production-ready"""

    def _template_advisory(self, user_input: str, context: Dict) -> str:
        """Advisory/Recommendation prompt template."""
        return f"""Task: Provide strategic advice or recommendations
User Request: {user_input}

Context: {context['complexity'].upper()} complexity
Language: {'German' if context['is_german'] else 'English'}

Requirements:
1. Analyze the situation thoroughly
2. Consider multiple perspectives
3. Provide actionable recommendations
4. Explain rationale
5. Highlight pros and cons

Output Format:
- Situation Assessment
- Key Challenges/Opportunities
- Recommended Actions
  - Recommendation 1: [Action] (Why?)
  - Recommendation 2: [Action] (Why?)
  - etc.
- Expected Outcomes
- Implementation Timeline
- Success Metrics

Tone: Strategic, confident, consultative
Detail Level: {context['complexity'].upper()}"""

    def _template_communication(self, user_input: str, context: Dict) -> str:
        """Communication prompt template."""
        return f"""Task: Create communication or message
User Request: {user_input}

Context: {context['complexity'].upper()} complexity
Language: {'German' if context['is_german'] else 'English'}

Requirements:
1. Write clear, professional communication
2. Tailor tone to audience
3. Include all necessary details
4. Drive desired action
5. Maintain relationship

Output Format:
- Subject/Opening
- Body
  - Main Message
  - Details/Context
  - Call to Action
- Closing
- Optional: Alternative versions

Tone: Professional, warm, clear
Detail Level: {context['complexity'].upper()}"""

    def _template_analysis(self, user_input: str, context: Dict) -> str:
        """Analysis prompt template."""
        return f"""Task: Analyze and evaluate
User Request: {user_input}

Context: {context['complexity'].upper()} complexity
Language: {'German' if context['is_german'] else 'English'}

Requirements:
1. Break down into components
2. Examine each element thoroughly
3. Identify relationships
4. Draw conclusions
5. Support with evidence

Output Format:
- Overview
- Detailed Analysis
  - Component 1: [Analysis]
  - Component 2: [Analysis]
  - etc.
- Patterns & Relationships
- Conclusions
- Implications

Tone: Analytical, objective, thorough
Detail Level: {context['complexity'].upper()}"""

    def _template_creative(self, user_input: str, context: Dict) -> str:
        """Creative prompt template."""
        return f"""Task: Create something original and innovative
User Request: {user_input}

Context: {context['complexity'].upper()} complexity
Language: {'German' if context['is_german'] else 'English'}

Requirements:
1. Think creatively and innovatively
2. Consider multiple angles
3. Be original and unique
4. Make it compelling
5. Ensure feasibility

Output Format:
- Concept Overview
- Detailed Description
- Key Features/Elements
- Why This Works
- Implementation Notes
- Next Steps

Tone: Creative, engaging, inspiring
Detail Level: {context['complexity'].upper()}"""

    def _template_optimization(self, user_input: str, context: Dict) -> str:
        """Optimization prompt template."""
        return f"""Task: Optimize and improve existing solution
User Request: {user_input}

Context: {context['complexity'].upper()} complexity
Language: {'German' if context['is_german'] else 'English'}

Requirements:
1. Identify current limitations
2. Analyze performance metrics
3. Propose improvements
4. Quantify benefits
5. Plan implementation

Output Format:
- Current State Assessment
- Bottlenecks Identified
- Proposed Optimizations
  - Optimization 1: [Change] (Benefit: X%)
  - Optimization 2: [Change] (Benefit: Y%)
  - etc.
- Implementation Plan
- Expected Results
- Metrics to Track

Tone: Results-focused, practical, data-driven
Detail Level: {context['complexity'].upper()}"""

    def _template_default(self, user_input: str, context: Dict) -> str:
        """Default/Generic prompt template."""
        return f"""Task: Complete user request
User Request: {user_input}

Context: {context['complexity'].upper()} complexity
Language: {'German' if context['is_german'] else 'English'}

Requirements:
1. Understand the core request
2. Provide comprehensive response
3. Be clear and organized
4. Include examples if helpful
5. Suggest next steps

Output Format:
- Executive Summary
- Detailed Response
- Key Points
- Examples (if relevant)
- Next Steps/Recommendations

Tone: Professional, helpful, clear
Detail Level: {context['complexity'].upper()}"""

    def optimize(self, user_input: str) -> Dict:
        """Main optimization pipeline."""
        logger.info(f"📝 Optimizing prompt: {user_input[:50]}...")

        # Step 1: Categorize
        category = self.categorize_input(user_input)

        # Step 2: Extract context
        context = self.extract_context(user_input, category)

        # Step 3: Create optimized prompt
        optimized_prompt = self.create_optimized_prompt(user_input, category, context)

        # Step 4: Store in history
        result = {
            "timestamp": datetime.now().isoformat(),
            "user_input": user_input,
            "context": context,
            "optimized_prompt": optimized_prompt,
            "category": category.value,
            "complexity": context["complexity"]
        }

        self.history["optimizations"].append(result)
        self._save_history()

        logger.info(f"✅ Optimization complete: {category.value} ({context['complexity']})")

        return result


def main():
    """CLI interface for prompt optimization."""
    print("\n" + "="*70)
    print("🧠 JARVIS PROMPT OPTIMIZATION ENGINE")
    print("="*70 + "\n")

    optimizer = PromptOptimizer()

    print("📝 Input your request (or 'exit' to quit):\n")

    while True:
        user_input = input("You: ").strip()

        if user_input.lower() == "exit":
            print("\n👋 Goodbye!\n")
            break

        if not user_input:
            continue

        # Optimize prompt
        result = optimizer.optimize(user_input)

        print("\n" + "-"*70)
        print(f"📊 Analysis:")
        print(f"   Category: {result['context']['category'].upper()}")
        print(f"   Complexity: {result['complexity'].upper()}")
        print(f"   Language: {'German' if result['context']['is_german'] else 'English'}")
        print("\n" + "-"*70)
        print("✨ Optimized Prompt:")
        print("-"*70)
        print(result['optimized_prompt'])
        print("\n" + "-"*70)
        print()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        # CLI mode with argument
        user_input = " ".join(sys.argv[1:])
        optimizer = PromptOptimizer()
        result = optimizer.optimize(user_input)

        print("\n" + "="*70)
        print("✨ OPTIMIZED PROMPT")
        print("="*70)
        print(result['optimized_prompt'])
        print("="*70 + "\n")
    else:
        # Interactive mode
        main()
