// A question as the ballot box sends it: mirrors ballot_box.questions.QuestionSpec.

export interface NOfMQuestion {
  kind: 'n_of_m';
  title: string;
  description: string;
  answers: string[];
  /** 0 allows a blank vote. */
  min_choices: number;
  max_choices: number;
  encoding: 'n_of_m/v1';
  method: 'choice_count/v1';
}

export type QuestionSpec = NOfMQuestion;

/** For n-out-of-m questions: the chosen answer indices. */
export type Answer = number[];
