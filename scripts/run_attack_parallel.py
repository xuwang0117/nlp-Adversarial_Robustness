"""
A command line parser to run an attack from user specifications.
"""

import os
import textattack
import time
import torch
import tqdm

from run_attack_args_helper import *

def set_env_variables(gpu_id):
    # Only use one GPU, if we have one.
    if 'CUDA_VISIBLE_DEVICES' not in os.environ:
        os.environ['CUDA_VISIBLE_DEVICES'] = str(gpu_id)
    # Disable tensorflow logs, except in the case of an error.
    if 'TF_CPP_MIN_LOG_LEVEL' not in os.environ:
        os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
    # Cache TensorFlow Hub models here, if not otherwise specified.
    if 'TFHUB_CACHE_DIR' not in os.environ:
        os.environ['TFHUB_CACHE_DIR'] = os.path.expanduser('~/.cache/tensorflow-hub')

def attack_from_queue(args, in_queue, out_queue):
    gpu_id = torch.multiprocessing.current_process()._identity[0] - 2
    print('Using GPU #' + str(gpu_id))
    set_env_variables(gpu_id)
    model, attack = parse_model_and_attack_from_args(args)
    while not in_queue.empty():
        try: 
            label, text = in_queue.get()
            results_gen = attack.attack_dataset([(label, text)], num_examples=1)
            result = next(results_gen)
            out_queue.put(result)
        except Exception as e:
            out_queue.put(e)
            exit()

def main():
    pytorch_multiprocessing_workaround()
    # This makes `args` a namespace that's sharable between processes.
    # We could do the same thing with the model, but it's actually faster
    # to let each thread have their own copy of the model.
    args = torch.multiprocessing.Manager().Namespace(
        **vars(get_args())
    )
    start_time = time.time()
    
    attack_logger = parse_logger_from_args(args)
    
    # We reserve the first GPU for coordinating workers.
    num_gpus = torch.cuda.device_count()
    dataset = DATASET_BY_MODEL[args.model](offset=args.num_examples_offset)
    
    print(f'Running on {num_gpus} GPUs')
    load_time = time.time()

    if args.interactive:
        raise RuntimeError('Cannot run in parallel if --interactive set')
    
    in_queue = torch.multiprocessing.Queue()
    out_queue =  torch.multiprocessing.Queue()
    # Add stuff to queue.
    for _ in range(args.num_examples):
        label, text = next(dataset)
        in_queue.put((label, text))
    # Start workers.
    pool = torch.multiprocessing.Pool(
        num_gpus, 
        attack_from_queue, 
        (args, in_queue, out_queue)
    )
    # Log results asynchronously and update progress bar.
    num_results = 0
    pbar = tqdm.tqdm(total=args.num_examples)
    while num_results < args.num_examples:
        result = out_queue.get(block=True)
        if isinstance(result, Exception):
            raise result
        attack_logger.log_result(result)
        if (not args.attack_n) or (not isinstance(result, textattack.attack_results.SkippedAttackResult)):
            pbar.update()
            num_results += 1
        else:
            label, text = next(dataset)
            in_queue.put((label, text))
    pbar.close()
    print()
    # Enable summary stdout.
    if args.disable_stdout:
        attack_logger.enable_stdout()
    attack_logger.log_summary()
    attack_logger.flush()
    print()
    finish_time = time.time()
    print(f'Attack time: {time.time() - load_time}s')

def pytorch_multiprocessing_workaround():
    # This is a fix for a known bug
    try:
        torch.multiprocessing.set_start_method('spawn')
    except RuntimeError:
        pass

if __name__ == '__main__': main()