# coding: utf-8

# general library imports
import cPickle, gzip, time, os, sys, pdb, json, datetime
import numpy
import scipy.sparse
# import theano
import theano
import theano.tensor as T
import theano.sparse
from theano.tensor.shared_randomstreams import RandomStreams
#sys.path.extend(['/home/fujikawa/lib/python/other/pylearn2/pylearn2', '/home/fujikawa/StockPredict/src/deeplearning/dataset'])

# import my library
#from XOR import XOR
from dataset.Nikkei import Nikkei

# outdir = '/home/fujikawa/StockPredict/src/deeplearning/experiment/Model/sae.pkl'

class SparseAutoencoder(object):
    

    def __init__(self, input=None, n_visible=784, n_hidden=500, sp_penalty=0.03, p=0.02, beta=0., reg_weight=0.001,
                 W=None, bhid=None, bvis=None, params = None, corruption_level=0):
        """
        Initialize the dA class by specifying the number of visible units (the
        dimension d of the input ), the number of hidden units ( the dimension
        d' of the latent or hidden space ) and the corruption level. The
        constructor also receives symbolic variables for the input, weights and
        bias. Such a symbolic variables are useful when, for example the input
        is the result of some computations, or when weights are shared between
        the dA and an MLP layer. When dealing with SdAs this always happens,
        the dA on layer 2 gets as input the output of the dA on layer 1,
        and the weights of the dA are used in the second stage of training
        to construct an MLP.

        :type numpy_rng: numpy.random.RandomState
        :param numpy_rng: number random generator used to generate weights

        :type theano_rng: theano.tensor.shared_randomstreams.RandomStreams
        :param theano_rng: Theano random generator; if None is given one is
                     generated based on a seed drawn from `rng`

        :type input: theano.tensor.TensorType
        :param input: a symbolic description of the input or None for
                      standalone dA

        :type n_visible: int
        :param n_visible: number of visible units

        :type n_hidden: int
        :param n_hidden:  number of hidden units

        :type W: theano.tensor.TensorType
        :param W: Theano variable pointing to a set of weights that should be
                  shared belong the dA and another architecture; if dA should
                  be standalone set this to None

        :type bhid: theano.tensor.TensorType
        :param bhid: Theano variable pointing to a set of biases values (for
                     hidden units) that should be shared belong dA and another
                     architecture; if dA should be standalone set this to None

        :type bvis: theano.tensor.TensorType
        :param bvis: Theano variable pointing to a set of biases values (for
                     visible units) that should be shared belong dA and another
                     architecture; if dA should be standalone set this to None


        """

        numpy_rng = numpy.random.RandomState(123)
        if params != None:
            W = theano.shared(params['W'], name='W', borrow=True)
            bvis = theano.shared(params['b_vis'], borrow=True)
            bhid = theano.shared(params['b_hid'], name='b', borrow=True)
            self.n_visible = params['n_visible']
            self.n_hidden = params['n_hidden']
            self.reg_weight = params['reg_weight']
            self.p = params['p']
            self.sp_penalty = params['sp_penalty']
            self.beta = params['beta']
            self.epoch = params['epoch']
            theano_rng = params['theano_rng']
            self.corruption_level = params['corruption_level']

        else:
            self.n_visible = n_visible
            self.n_hidden = n_hidden
            self.reg_weight = reg_weight
            self.p = p
            self.sp_penalty = sp_penalty
            self.beta = beta
            self.epoch = 0
            theano_rng = RandomStreams(numpy_rng.randint(2 ** 30))
            self.corruption_level = corruption_level
        

        
        
        # create a Theano random generator that gives symbolic random values
        

        # note : W' was written as `W_prime` and b' as `b_prime`
        if not W:
            # W is initialized with `initial_W` which is uniformely sampled
            # from -4*sqrt(6./(n_visible+n_hidden)) and
            # 4*sqrt(6./(n_hidden+n_visible))the output of uniform if
            # converted using asarray to dtype
            # theano.config.floatX so that the code is runable on GPU
            initial_W = numpy.asarray(numpy_rng.uniform(
                      low=-1 * numpy.sqrt(6. / (n_hidden + n_visible)),
                      high=1 * numpy.sqrt(6. / (n_hidden + n_visible)),
                      size=(n_visible, n_hidden)), dtype=theano.config.floatX)
            W = theano.shared(value=initial_W, name='W', borrow=True)

        if not bvis:
            bvis = theano.shared(value=numpy.zeros(n_visible,
                                         dtype=theano.config.floatX),
                                 borrow=True)

        if not bhid:
            bhid = theano.shared(value=numpy.zeros(n_hidden,
                                                   dtype=theano.config.floatX),
                                 name='b',
                                 borrow=True)

        self.W = W
        # b corresponds to the bias of the hidden
        self.b = bhid
        # b_prime corresponds to the bias of the visible
        self.b_prime = bvis
        # tied weights, therefore W_prime is W transpose
        self.W_prime = self.W.T
        self.theano_rng = theano_rng
        # if no input is given, generate a variable representing the input
        if input == None:
            # we use a matrix because we expect a minibatch of several
            # examples, each example being a row
            self.input = T.dmatrix(name='input')
        else:
            self.input = input

        self.params = [self.W, self.b, self.b_prime]

        matrix = T.matrix()
        vector = T.vector()        
        self.get_propup_vector = theano.function([vector], self.get_hidden_values(vector))
        self.get_propup_matrix = theano.function([matrix], self.get_hidden_values(matrix))
        matrix_maxpool = T.matrix()
        matrix_meanpool = T.matrix()
        self.get_maxpool = theano.function([matrix_maxpool], T.max(self.get_hidden_values(matrix_maxpool), axis=0))
        self.get_meanpool = theano.function([matrix_meanpool], T.mean(self.get_hidden_values(matrix_meanpool), axis=0))

    def get_corrupted_input(self, input, corruption_level):
        """This function keeps ``1-corruption_level`` entries of the inputs the
        same and zero-out randomly selected subset of size ``coruption_level``
        Note : first argument of theano.rng.binomial is the shape(size) of
               random numbers that it should produce
               second argument is the number of trials
               third argument is the probability of success of any trial

                this will produce an array of 0s and 1s where 1 has a
                probability of 1 - ``corruption_level`` and 0 with
                ``corruption_level``

                The binomial function return int64 data type by
                default.  int64 multiplicated by the input
                type(floatX) always return float64.  To keep all data
                in floatX when floatX is float32, we set the dtype of
                the binomial to floatX. As in our case the value of
                the binomial is always 0 or 1, this don't change the
                result. This is needed to allow the gpu to work
                correctly as it only support float32 for now.

        """
        return  self.theano_rng.binomial(size=input.shape, n=1,
                                         p=1 - corruption_level,
                                         dtype=theano.config.floatX) * input

    def activate_function(self, arg):
        return T.tanh(arg)
    def get_hidden_values(self, input):
        """ Computes the values of the hidden layer """
        return T.nnet.sigmoid(T.dot(input, self.W) + self.b)

    def get_reconstructed_input(self, hidden):
        """Computes the reconstructed input given the values of the
        hidden layer

        """
        return  T.nnet.sigmoid(T.dot(hidden, self.W_prime) + self.b_prime)

    def get_cost_updates(self, corruption_level, learning_rate):
        """ This function computes the cost and the updates for one trainng
        step of the dA """

        tilde_x = self.get_corrupted_input(self.input, corruption_level)
        y = self.get_hidden_values(tilde_x)
        z = self.get_reconstructed_input(y)

        L = - T.sum(self.input * T.log(z) + (1 - self.input) * T.log(1 - z), axis=1)

        cost = T.mean(L)

        l2_w, l2_h = self.get_norm_penalty(self.input, isUpdate=True)
        
        cost += 1e-2 * l2_w
        cost += l2_h

        # compute the gradients of the cost of the `dA` with respect
        # to its parameters
        gparams = T.grad(cost, self.params)
        # generate the list of updates
        updates =  []
        for param, gparam in zip(self.params, gparams):
            updates.append((param, param - learning_rate * gparam))

        return (cost, updates)

    def get_cost_updates2(self, corruption_level, learning_rate, pre_model):
        """ This function computes the cost and the updates for one trainng
        step of the dA """
        input2 = pre_model.get_propup_matrix(self.input)
        tilde_x = self.get_corrupted_input(self.input, corruption_level)
        y = self.get_hidden_values(tilde_x)
        z = self.get_reconstructed_input(y)

        L = - T.sum(self.input * T.log(z) + (1 - self.input) * T.log(1 - z), axis=1)

        cost = T.mean(L)

        l2_w, l2_h = self.get_norm_penalty(self.input, isUpdate=True)
        
        cost += 1e-2 * l2_w
        cost += l2_h

        # compute the gradients of the cost of the `dA` with respect
        # to its parameters
        gparams = T.grad(cost, self.params)
        # generate the list of updates
        updates =  []
        for param, gparam in zip(self.params, gparams):
            updates.append((param, param - learning_rate * gparam))

        return (cost, updates)


    def get_norm_penalty(self, x, isUpdate=True):

        def l1(param):
            return T.sum(T.abs(param))
        def l2(param):
            return T.sum(param ** 2)
        def l2_a0(param):
            return T.sum(param ** 2)
        def KL(p, p_hat):
            return T.sum((p * T.log(p / p_hat)) + ((1 - p) * T.log((1 - p) / (1 - p_hat))))

        l2_w = 0
        l2_h = 0
        # l1_w = l1(self.W)
        l2_w = self.reg_weight * l2(self.W)
        # l2_w = 0
        # l2_h = self.reg_weight * l2(self.get_propup_matrix(x))
        # l2_h = 0
        # l1_h = l1(self.get_propup_matrix(x))
        if isUpdate == True:
            l2_h = self.reg_weight * l2(self.get_hidden_values(x))
        else:
            l2_h = self.reg_weight * l2(self.get_propup_matrix(x))
        
        return l2_w, l2_h
    def output_params(self):
        print 
        W = numpy.asarray(self.W.get_value())
        b_hid = numpy.asarray(self.b.get_value())
        b_vis = numpy.asarray(self.b_prime.get_value())
        params = {
            'W' : W,
            'b_hid' : b_hid,
            'b_vis' : b_vis,
            'n_visible' : self.n_visible,
            'n_hidden' : self.n_hidden,
            'reg_weight' : self.reg_weight,
            'p' : self.p,
            'sp_penalty' : self.sp_penalty,
            'beta' : self.beta,
            'epoch' : self.epoch,
            'theano_rng' : self.theano_rng,
            'corruption_level' : self.corruption_level

        }
        return params
        # self.W_prime = theano.shared(numpy.asarray(self.W_prime))
        # self.b_prime = theano.shared(numpy.asarray(self.b_prime))
        # self.n_visible = n_visible
        # self.n_hidden = n_hidden
        # self.reg_weight = reg_weight
        # self.p = p
        # self.sp_penalty = sp_penalty
        # self.beta = beta
        # self.epoch = 0
        # self.W = W
        # self.b = bhid
        # self.b_prime = bvis
        # self.W_prime = self.W.T
        # self.theano_rng = theano_rng



def train_sae(input=None, model=None, dataset=None, learning_rate=1e-2, training_epochs=15, batch_size=50, outdir=''):

    """
    This demo is tested on MNIST

    :type learning_rate: float
    :param learning_rate: learning rate used for training the DeNosing
                          AutoEncoder

    :type training_epochs: int
    :param training_epochs: number of epochs used for training

    :type dataset: string
    :param dataset: path to the picked dataset

    """

    
    print 'start to train SparseAutoencoder'
    # datasets = XOR()
    if dataset == None:
        print 'dataset is not provided'
        sys.exit()
    

    n_train_batches = dataset.phase1['train'].get_value(borrow=True).shape[0] / batch_size

    # allocate symbolic variables for the data
    index = T.lscalar()    # index to a [mini]batch
    x = input
    # x = T.matrix('x')  # the data is presented as rasterized images
    print n_train_batches

    #####################################
    # BUILDING THE MODEL CORRUPTION 30% #
    #####################################


    # sae = SparseAutoencoder(numpy_rng=rng, theano_rng=theano_rng, input=x,
    #         n_visible=dataset.phase1_input_size, n_hidden=n_hidden)

    cost, updates = model.get_cost_updates(corruption_level=model.corruption_level,
                                        learning_rate=learning_rate)

    trainer = theano.function([index], cost, updates=updates,
         givens={x: dataset.get_batch_design(index, batch_size, dataset.phase1['train'])})

    start_time = time.clock()

    ############
    # TRAINING #
    ############
    print 'write file: ' + outdir
    # go through training epochs
    
    x_example = dataset.get_batch_design(0, 3000, dataset.phase1['valid']).eval()

    for epoch in xrange(training_epochs):
        # go through trainng set
        mean_cost = []
        previous_cost = 0
        for batch_index in xrange(n_train_batches):
            while(True):
                try:
                    mean_cost.append(trainer(batch_index))
                    msg = '%s e: %d, b: %d, c: %.2f, '% (str(datetime.datetime.now().strftime("%m/%d %H:%M")), epoch, batch_index, numpy.mean(mean_cost))
                    if batch_index % 10000 == 0:
                        # test_propup = model.get_hidden_values(dataset.get_batch_design(0, 100, dataset.phase1['valid'])).eval()
                        test_propup = model.get_propup_matrix(x_example)
                        msg += 'sp: %.2f, mm: %.2f~%.2f, ' % (test_propup.mean(axis=1).mean(), test_propup.max(axis=0).mean(), test_propup.min(axis=0).mean())
                        msg += '%s' % str(numpy.histogram(test_propup.mean(axis=0), range=[0,1])[0])
                        # msg = ('%s   epoch : %d, batch : %d, cost : %.2f, cost diff: %.3f, l2: %.2f, KL: %.2f, sparsity: %.2f, max-min: %.2f, std: %.2f, output: %s'
                        #       % (str(datetime.datetime.now()), model.epoch, batch_index, numpy.mean(c), numpy.mean(c) - previous_cost,
                        #         l2(), float(KL(0.02, test_propup.mean())),
                        #         test_propup.mean(), (test_propup.max(axis=0) - test_propup.min(axis=0)).mean(), 
                        #         test_propup.std(axis=0).mean(), outdir.split('/')[len(outdir.split('/')) - 1]))
                   
                    sys.stdout.write("\r%s" % msg)
                    sys.stdout.flush()
                    break
                    # # print T.mean(sae.get_hidden_values(dataset.get_batch_design(0, 100, dataset.valid))).eval()
                    
                except KeyboardInterrupt:
                    pdb.set_trace()
                
        model.epoch += 1
        params = model.output_params()
        print
        while(True):
            try:
                f_out = open(outdir, 'w')
                f_out.write(cPickle.dumps(params, 1))
                f_out.close()
                break
            except:
                print 'File could not be written...'
                pdb.set_trace()
        
    # outdir = '/home/fujikawa/StockPredict/src/deeplearning/experiment/Model/sae.pkl'
    while(True):
        try:
            f_out = open(outdir, 'w')
            f_out.write(cPickle.dumps(params, 1))
            f_out.close()
            break
        except:
            pdb.set_trace()

    # pdb.set_trace()
    end_time = time.clock()

    training_time = (end_time - start_time)

def train_sae2(input=None, model=None, pre_model=None, dataset=None, learning_rate=1e-2, training_epochs=15, batch_size=50, outdir=''):

    """
    This demo is tested on MNIST

    :type learning_rate: float
    :param learning_rate: learning rate used for training the DeNosing
                          AutoEncoder

    :type training_epochs: int
    :param training_epochs: number of epochs used for training

    :type dataset: string
    :param dataset: path to the picked dataset

    """

    
    print 'start to train SparseAutoencoder'
    # datasets = XOR()
    if dataset == None:
        print 'dataset is not provided'
        sys.exit()
    

    n_train_batches = dataset.phase1['train'].get_value(borrow=True).shape[0] / batch_size

    # allocate symbolic variables for the data
    index = T.lscalar()    # index to a [mini]batch
    x = input
    # x = T.matrix('x')  # the data is presented as rasterized images
    print n_train_batches

    #####################################
    # BUILDING THE MODEL CORRUPTION 30% #
    #####################################


    # sae = SparseAutoencoder(numpy_rng=rng, theano_rng=theano_rng, input=x,
    #         n_visible=dataset.phase1_input_size, n_hidden=n_hidden)

    cost, updates = model.get_cost_updates(corruption_level=model.corruption_level,
                                        learning_rate=learning_rate, pre_model=pre_model)
    pdb.set_trace()
    trainer = theano.function([index], cost, updates=updates,
         givens={x: pre_model.get_propup_matrix(dataset.get_batch_design(index, batch_size, dataset.phase1['train']).eval())})

    start_time = time.clock()

    ############
    # TRAINING #
    ############
    print 'write file: ' + outdir
    # go through training epochs
    
    x_example = pre_model.get_propup_matrix(dataset.get_batch_design(0, 3000, dataset.phase1['valid']).eval())

    for epoch in xrange(training_epochs):
        # go through trainng set
        mean_cost = []
        previous_cost = 0
        for batch_index in xrange(n_train_batches):
            while(True):
                try:
                    mean_cost.append(trainer(batch_index))
                    msg = '%s e: %d, b: %d, c: %.2f, '% (str(datetime.datetime.now().strftime("%m/%d %H:%M")), epoch, batch_index, numpy.mean(mean_cost))
                    if batch_index % 10000 == 0:
                        # test_propup = model.get_hidden_values(dataset.get_batch_design(0, 100, dataset.phase1['valid'])).eval()
                        test_propup = model.get_propup_matrix(x_example)
                        msg += 'sp: %.2f, mm: %.2f~%.2f, ' % (test_propup.mean(axis=1).mean(), test_propup.max(axis=0).mean(), test_propup.min(axis=0).mean())
                        msg += '%s' % str(numpy.histogram(test_propup.mean(axis=0), range=[0,1])[0])
                        # msg = ('%s   epoch : %d, batch : %d, cost : %.2f, cost diff: %.3f, l2: %.2f, KL: %.2f, sparsity: %.2f, max-min: %.2f, std: %.2f, output: %s'
                        #       % (str(datetime.datetime.now()), model.epoch, batch_index, numpy.mean(c), numpy.mean(c) - previous_cost,
                        #         l2(), float(KL(0.02, test_propup.mean())),
                        #         test_propup.mean(), (test_propup.max(axis=0) - test_propup.min(axis=0)).mean(), 
                        #         test_propup.std(axis=0).mean(), outdir.split('/')[len(outdir.split('/')) - 1]))
                   
                    sys.stdout.write("\r%s" % msg)
                    sys.stdout.flush()
                    break
                    # # print T.mean(sae.get_hidden_values(dataset.get_batch_design(0, 100, dataset.valid))).eval()
                    
                except KeyboardInterrupt:
                    pdb.set_trace()
                
        model.epoch += 1
        params = model.output_params()
        print
        while(True):
            try:
                f_out = open(outdir, 'w')
                f_out.write(cPickle.dumps(params, 1))
                f_out.close()
                break
            except:
                print 'File could not be written...'
                pdb.set_trace()
        
    # outdir = '/home/fujikawa/StockPredict/src/deeplearning/experiment/Model/sae.pkl'
    while(True):
        try:
            f_out = open(outdir, 'w')
            f_out.write(cPickle.dumps(params, 1))
            f_out.close()
            break
        except:
            pdb.set_trace()

    # pdb.set_trace()
    end_time = time.clock()

    training_time = (end_time - start_time)


if __name__ == '__main__':

    dataset = Nikkei()
    index = T.lscalar()    # index to a [mini]batch
    x = T.matrix('x')  # the data is presented as rasterized images
    model = SparseAutoencoder(input=x, n_visible=dataset.phase1_input_size, n_hidden=100)
    train_sae(model=model, dataset=dataset,learning_rate=0.01)


