from typing import Any, Dict, List, Tuple

import difflib

import numpy as np
import pandas as pd
from loguru import logger

from litreading.config import (
    ASR_TRANSCRIPT_COL,
    DURATION_COL,
    HUMAN_TRANSCRIPT_COL,
    PROMPT_TEXT_COL,
)
from litreading.utils.text import (
    numbers_to_literals,
    recompose_asr_string_from_dict,
    remove_punctuation_from_string,
)


class LCSPreprocessor:
    prompt_col: str = PROMPT_TEXT_COL
    asr_transcript_col: str = ASR_TRANSCRIPT_COL
    human_transcript_col: str = HUMAN_TRANSCRIPT_COL
    duration_col: str = DURATION_COL

    def __init__(
        self,
        asr_string_recomposition: bool = False,
        to_lowercase: bool = True,
        remove_punctuation: bool = True,
        convert_num2words: bool = True,
        grade_mode: bool = False,
    ) -> None:
        """
        Args:
            asr_string_recomposition (bool, optional): Whether to recompose strings for text columns. Defaults to False.
            to_lowercase (bool, optional): Convert text to lowercase. Defaults to True.
            remove_punctuation (bool, optional): Remove punctuation from text. Defaults to True.
            convert_num2words (bool, optional): Convert numbers to words. Defaults to True.
        """
        self._preprocesssing_steps = {
            "asr_string_recomposition": asr_string_recomposition,
            "to_lowercase": to_lowercase,
            "remove_punctuation": remove_punctuation,
            "convert_num2words": convert_num2words,
        }
        self.grade_mode = grade_mode
        self.__steps_iter = None
        self._init_steps()

    @property
    def preprocessing_steps(self) -> Dict[str, bool]:
        return self._preprocesssing_steps

    def _init_steps(self) -> None:
        """Initialize preprocessing steps list based on attributes"""
        self._steps = [k for k, v in self.preprocessing_steps.items() if v]
        self._steps.append("compute_numerical_features")

    def _compute_and_log_step_msg(self, verbose) -> str:
        """Compute step message in logging

        Args:
            verbose (bool): If set to False, no message will be printed for each step
        """
        step_no, step_name = next(self.__steps_iter)
        if verbose:
            msg = f"[{self.__class__.__name__}] (step {step_no + 1} of {len(self._steps)}): {step_name}"
            logger.info(msg)

    def preprocess_data(
        self,
        df: pd.DataFrame,
        verbose: bool = True,
    ) -> pd.DataFrame:
        """Preprocess data and compute numerical features from text

        Args:
            df (pd.DataFrame): data to preprocesss. Must include all cols listed below.
            prompt_col (str): Defaults to PROMPT_TEXT_COL.
            asr_transcript_col (str): Defaults to ASR_TRANSCRIPT_COL.
            human_transcript_col (str): Defaults to HUMAN_TRANSCRIPT_COL.
            duration_col (str): Defaults to DURATION_COL.
            verbose (bool, optional): If True, will print a message at each preprocessing step.
                Defaults to True.

        Returns:
            pd.DataFrame: features computed from processed df
        """
        self.__steps_iter = iter(enumerate(self._steps))

        if verbose is False:
            logger.disable("litreading.preprocessor")

        data_ = df.copy()

        text_cols = [self.prompt_col, self.asr_transcript_col]
        if self.grade_mode is True:
            text_cols += self.human_transcript_col

        data_[text_cols] = self.preprocess_text(
            data_[text_cols], **self.preprocessing_steps, verbose=verbose
        )

        self._compute_and_log_step_msg(verbose)
        features = self.compute_numerical_features(
            data_, self.prompt_col, self.asr_transcript_col, self.duration_col
        )

        if verbose is False:
            logger.disable("litreading.preprocessor")

        return features

    def preprocess_text(
        self,
        data: pd.DataFrame,
        to_lowercase: bool = True,
        remove_punctuation: bool = True,
        convert_num2words: bool = True,
        asr_string_recomposition: bool = False,
        verbose: bool = True,
    ) -> pd.DataFrame:
        """Preprocess dataframe. All columns must be text. Nans are filled with ' '

        Args:
            data (pd.Dataframe): data to preprocesss
            to_lowercase (bool, optional): [description]. Defaults to True.
            remove_punctuation (bool, optional): [description]. Defaults to True.
            convert_num2words (bool, optional): [description]. Defaults to True.
            asr_string_recomposition (bool, optional): [description]. Defaults to False.
            verbose (bool, optional): If True, will print a message at each preprocessing step.
                Defaults to True.

        Returns:
            pd.DataFrame: processed text data
        """
        if asr_string_recomposition:
            self._compute_and_log_step_msg(verbose)
            data = data.applymap(recompose_asr_string_from_dict)

        if to_lowercase:
            self._compute_and_log_step_msg(verbose)
            data = data.applymap(lambda x: str(x).lower())

        if convert_num2words:
            self._compute_and_log_step_msg(verbose)
            data = data.applymap(numbers_to_literals)

        if remove_punctuation:
            self._compute_and_log_step_msg(verbose)
            data = data.applymap(remove_punctuation_from_string)

        data = data.fillna(" ")
        return data

    def compute_numerical_features(
        self, data: pd.DataFrame, prompt_col: str, asr_transcript_col: str, duration_col: str
    ) -> pd.DataFrame:
        """Compute numerical features such as:
            - number of words similar, added, removed, replaced
            - length of words means, stds

        Args:
            data (pd.DataFrame): processed text data with duration col to compute features
            prompt_col (str): [description]
            asr_transcript_col (str): [description]
            duration_col (str): [description]

        Returns:
            pd.DataFrame: features computed from processed df
        """
        diff_list_df = self.compute_differ_lists(data, col_1=prompt_col, col_2=asr_transcript_col)
        words_count = diff_list_df.apply(lambda x: pd.Series(self.get_words_count(x)))

        features = pd.DataFrame.from_dict(words_count).add_suffix("_words")
        features["asr_word_count"] = data[asr_transcript_col].apply(lambda x: len(x.split()))

        features = features.div(data[duration_col] / 60, axis=0)
        features = features.add_suffix("_pm")

        for col in [prompt_col, asr_transcript_col]:
            words_length_stats = data[col].apply(
                lambda x: pd.Series(self.get_words_length_stats(x))
            )
            words_length_stats.columns = [f"{col}_word_length_avg", f"{col}_word_length_std"]
            features = pd.concat([features, words_length_stats], axis=1)

        return features

    def compute_differ_lists(self, data: pd.DataFrame, col_1: str, col_2: str) -> pd.Series:
        """Compute Series of differ lists between two strings types columns

        Args:
            data (pd.DataFrame): [description]
            col_1 (str): name of base col
            col_2 (str): name of col to compare to base col

        Raises:
            TypeError: if col_1 or col_2 is not of type str

        Returns:
            pd.Series: df with one column differ list
        """
        if not (isinstance(col_1, str) and isinstance(col_2, str)):
            raise TypeError("col_1 and col_2 should be strings from data columns headers")

        logger.debug("Computing differences list for %s v. %s", col_1, col_2)
        differ_list_df = data.apply(
            lambda x: self.longest_common_subsequence(x[col_1], x[col_2]), axis=1
        )
        return differ_list_df

    @staticmethod
    def longest_common_subsequence(str_a: str, str_b: str, split_car: str = " ") -> List[str]:
        """Compute differ list between two strings using longest common subsequence algorithm

        Args:
            str_a (str), str_b (str): [description]
            split_car (str, optional): Defaults to " ".

        Raises:
            TypeError: if str_a or str_b are not str type

        Returns:
            List[str]: list of same and different parts (see difflib.Differ.compare)
        """
        if not isinstance(str_a, str) or not isinstance(str_b, str):
            raise TypeError("Compared strings must be of string type")

        differ_list_ = difflib.Differ().compare(str_a.split(split_car), str_b.split(split_car))
        differ_list = list(differ_list_)

        # if a lot of characters at the end were added or removed from prompt
        # then delete them from differ list
        to_be_removed = differ_list[-1][0]
        if to_be_removed != " ":
            while differ_list[-1][0] == to_be_removed and len(differ_list) >= 1:
                differ_list.pop()

        return differ_list

    @staticmethod
    def get_words_count(differ_list: List[str]) -> Tuple[Dict[str, Any], Dict[str, List[str]]]:
        """Return number of correct, added, removed, replaced words and dict of errors

        Args:
            differ_list (List[str]): [description]

        Returns:
            Dict[str, Any]: dict with number of words correct, added, removed, replaced
            Dict[str, List[str]]: list of replaced words detected (errors_dict)
        """
        correct = 0
        errors_dict = {PROMPT_TEXT_COL: [], "transcript": []}
        skip_next = 0
        n = len(differ_list)
        added = 0
        removed = 0
        for i, word in enumerate(differ_list):
            if skip_next > 0:
                skip_next -= 1
                continue  # when the word has already been added to the error dict
            if word[0] == " ":
                correct += 1  # + 1 if word correct
            elif i < n - 2:  # keep track of mistakes
                if word[0] == "+":
                    added += 1
                elif word[0] == "-":
                    removed += 1
                j = 1
                while i + j < n and differ_list[i + j][0] == "?":  # account for ? in skip_next
                    j += 1
                # two cases for replaced words: + - or - +
                plus_minus = word[0] == "+" and differ_list[i + j][0] == "-"
                minus_plus = word[0] == "-" and differ_list[i + j][0] == "+"
                skip_next = (plus_minus or minus_plus) * j
                if plus_minus:
                    errors_dict[PROMPT_TEXT_COL] += [word.replace("+ ", "")]
                    errors_dict["transcript"] += [differ_list[i + j].replace("- ", "")]
                elif minus_plus:
                    errors_dict[PROMPT_TEXT_COL] += [word.replace("- ", "")]
                    errors_dict["transcript"] += [differ_list[i + j].replace("+ ", "")]

        words_count = {
            "correct": correct,
            "added": added,
            "removed": removed,
            "replaced": len(errors_dict[PROMPT_TEXT_COL]),
        }
        return words_count

    @staticmethod
    def get_words_length_stats(s: str, sep: str = " ") -> Tuple[int, int]:
        """Return the avg and std of length of words in a string s, with separator sep.

        Args:
            s (str)
            sep (str, optional): [description]. Defaults to " ".

        Returns:
            int: mean
            int: std
        """
        s = s.split(sep)
        if len(s) == 0:
            return 0, 0
        s = [len(word) for word in s]
        mean = np.mean(s)
        std = np.std(s)
        return mean, std
