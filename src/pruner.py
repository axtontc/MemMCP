# src/pruner.py
"""
Context pruning daemon for MemMCP.
Estimates tokens and prunes context strings down to limit while retaining relevant information.
"""

import math
import re
from typing import Dict, List, Set, Tuple


class ContextPruner:
    """
    Manages context window pruning based on TF-IDF scoring and recency.
    """

    def estimate_tokens(self, text: str) -> int:
        """
        Estimates the number of tokens in the text using a standard approximation.
        Based on word count / 0.75.
        
        Args:
            text: The text to estimate token count for.
            
        Returns:
            The estimated token count.
        """
        words = text.split()
        if not words:
            return 0
        return math.ceil(len(words) / 0.75)

    def prune_context(
        self,
        context_text: str,
        max_tokens: int = 4000,
        threshold_tokens: int = 20000
    ) -> str:
        """
        Prunes the context text to fit within max_tokens if the estimated tokens exceed threshold_tokens.
        Prioritizes retaining the first and last lines (system prompts/latest messages)
        along with sentences/lines having the highest TF-IDF scores and a recency boost.
        
        Args:
            context_text: The full context string.
            max_tokens: The target token limit after pruning.
            threshold_tokens: The threshold token limit triggering pruning.
            
        Returns:
            The pruned (or original, if within limit) context string.
        """
        if self.estimate_tokens(context_text) <= threshold_tokens:
            return context_text

        lines = context_text.splitlines()
        if not lines:
            return ""

        # Calculate TF-IDF of words across all lines
        tokenized_lines: List[List[str]] = []
        word_doc_freq: Dict[str, int] = {}
        for line in lines:
            words = re.findall(r"\b\w+\b", line.lower())
            tokenized_lines.append(words)
            unique_words = set(words)
            for w in unique_words:
                word_doc_freq[w] = word_doc_freq.get(w, 0) + 1

        num_docs = len(lines)
        word_idf: Dict[str, float] = {}
        for w, df in word_doc_freq.items():
            word_idf[w] = math.log((num_docs + 1) / (df + 1)) + 1.0

        line_scores: List[Tuple[int, float]] = []
        for idx, words in enumerate(tokenized_lines):
            if not words:
                line_scores.append((idx, 0.0))
                continue

            score = sum(words.count(w) * word_idf.get(w, 0.0) for w in words)
            score = score / math.log(len(words) + 1)

            # Add a recency boost to prioritize later lines
            recency_boost = (idx / num_docs) * 2.0
            line_scores.append((idx, score + recency_boost))

        # Always try to keep the first and last lines (system instructions and latest query)
        always_keep_indices = {0, len(lines) - 1} if len(lines) > 1 else {0}
        candidates = [item for item in line_scores if item[0] not in always_keep_indices]
        candidates.sort(key=lambda x: x[1], reverse=True)

        # Select lines that fit within max_tokens
        always_keep_list = sorted(list(always_keep_indices))
        current_tokens = 0
        valid_always_keep: Set[int] = set()
        
        for idx in always_keep_list:
            line_tok = self.estimate_tokens(lines[idx])
            if current_tokens + line_tok <= max_tokens:
                valid_always_keep.add(idx)
                current_tokens += line_tok
            else:
                break

        # If even the first line doesn't fit, truncate it to fit max_tokens
        if not valid_always_keep:
            first_line_words = lines[0].split()
            fitted_words: List[str] = []
            curr_tok = 0
            for w in first_line_words:
                tok = math.ceil(1 / 0.75)
                if curr_tok + tok <= max_tokens:
                    fitted_words.append(w)
                    curr_tok += tok
                else:
                    break
            return " ".join(fitted_words)

        selected_indices = valid_always_keep
        for idx, _ in candidates:
            line_tok = self.estimate_tokens(lines[idx])
            if current_tokens + line_tok <= max_tokens:
                selected_indices.add(idx)
                current_tokens += line_tok

        # Reconstruct the context in the original line order
        pruned_lines = [lines[idx] for idx in sorted(selected_indices)]
        return "\n".join(pruned_lines)
