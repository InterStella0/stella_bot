from __future__ import annotations
import numpy as np
from typing import Optional


class PrefixNeuralNetwork:
    """This Neural Network contains 2x1 input neuron, 1x3 hidden neuron, 1x1 output neuron"""
    def __init__(self, x: Optional[np.array] = None, y: Optional[np.array] = None, weight1: Optional[np.array] = None,
                 weight2: Optional[np.array] = None):
        """When trainable is False, it is unable to train. This NN is supervised learning rather than unsupervised."""
        trainable = True
        if x is None:
            trainable = False

        self.input = x
        if weight1 is None:
            weight1 = np.random.rand(self.input.shape[1], 3)

        if weight2 is None:
            weight2 = np.random.rand(3, 1)

        self.weights1 = weight1
        self.weights2 = weight2

        if trainable:
            self.y = y
            self.output = np.zeros(y.shape)

        self.layer1 = None
        self.layer2 = None

    @staticmethod
    def sigmoid_activation(x):
        """Using sigmoid as activation function. This is to describe the uncertainty of a prefix usage vs time relationship."""
        return 1.0 / (1 + np.exp(-x))

    @classmethod
    def from_weight(cls, weight1: np.array, weight2: np.array) -> PrefixNeuralNetwork:
        """This create an empty NeuralNetwork that cannot learn."""
        return cls(weight1=np.array(weight1), weight2=np.array(weight2))

    def calc_layer(self, layer, weight) -> np.array[float]:
        """Get the dot product and use sigmoid as activation function"""
        return self.sigmoid_activation(np.dot(layer, weight))

    def feedforward(self) -> None:
        """Calculate all layers"""
        self.layer1 = self.calc_layer(self.input, self.weights1)
        self.output = self.calc_layer(self.layer1, self.weights2)

    def backprop(self) -> None:  # Wrote docstring so i remember
        """Backprogration that uses chain rule.
            dy = σ'(w2 * σ'(w1 * x))

            All equation is in this photo, just in case i forgot lol
            https://miro.medium.com/max/700/1*7zxb2lfWWKaVxnmq2o69Mw.png
        """
        def times_derivative(left_side, layer):
            """Sigmoid derivative is for the weight calculation in backpropegration."""
            return left_side * (layer * (1.0 - layer))

        chain1 = times_derivative(2 * (self.y - self.output), self.output)
        d_weights2 = self.layer1.T @ chain1

        chain2 = times_derivative(chain1 @ self.weights2.T, self.layer1)
        d_weights1 = self.input.T @ chain2

        self.weights1 += d_weights1
        self.weights2 += d_weights2

    def train(self, epoch: Optional[int] = 100) -> None:
        """Self explanatory"""
        for e in range(epoch):
            self.feedforward()
            self.backprop()
            print("Epoch:", e)

    def fit(self, x: np.array) -> np.array[float]:
        """Gets the prediction for each input passed in numpy.array where element
           1st: prefix usage amount
           2nd: prefix last usage time
        """
        layer1 = self.calc_layer(x, self.weights1)
        result = self.calc_layer(layer1, self.weights2)
        return result
