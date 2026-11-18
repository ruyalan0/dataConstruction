"""Select the Isaac device by its native ordinal, with no CUDA visibility remapping."""


def simulator_environment(environment, gpu):
    if type(gpu) is not int or gpu < 0:
        raise ValueError('GPU ordinal must be a nonnegative integer')
    result = dict(environment)
    result.pop('CUDA_VISIBLE_DEVICES', None)
    result.update(ISAACSIM_GPU=str(gpu), LIVESTREAM='0', OMNI_KIT_ACCEPT_EULA='YES',
                  OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    return result


def launcher_options(gpu, options):
    result = dict(options)
    result['device'] = f'cuda:{gpu}'
    result['multi_gpu'] = False
    result['kit_args'] = (result.get('kit_args', '') +
                          f' --/renderer/activeGpu={gpu} --/renderer/multiGpu/enabled=false').strip()
    return result
